# 冻结配装对比的评分口径并复用公共评分公式。
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass
from hashlib import sha256
import json
from math import isfinite
from types import SimpleNamespace
from typing import Any, Mapping

from src.domain.characters.recommended_weights import base_weight_character_id
from src.optimizer.contracts import DIFF_ADDED, DIFF_ADDED_UIDS, DIFF_CHANGED, DIFF_REMOVED
from src.optimizer.scoring import ScoringEngine
from src.services.allocation.inputs.inventory import legacy_stat_name, legacy_stat_value
from src.domain.equipment.loadouts.virtual_equipment import grid_count_from_geometry


@dataclass(frozen=True)
class FrozenComparisonScorer:
    engine: ScoringEngine
    character_id: int
    dataset_id: str
    names: Mapping[str, str]
    weights: Mapping[str, float]
    main_weights: Mapping[str, float] | None
    zero_weight_stats: tuple[str, ...]
    max_weight: float
    static_database_path: str = ""
    static_schema_version: int = 0
    static_sha256: str = ""

    @classmethod
    def from_engine(cls, engine, *, character_id, weights, main_weights, attributes,
                    dataset_id, zero_weight_stats=(), frozen_engine=None, max_weight=None,
                    static_release=None):
        # 共享副本只能由同一次比较任务提供；外部原评分器后续变化不影响它。
        frozen = engine.frozen_copy() if frozen_engine is None else frozen_engine
        names = {
            str(row["attribute_id"]): legacy_stat_name(str(row["attribute_id"]))
            or ScoringEngine._scoring_property_name(row)
            for row in attributes
        }
        weights = deepcopy(dict(weights))
        mains = deepcopy(dict(main_weights)) if main_weights is not None else None
        zeros = tuple(str(name) for name in zero_weight_stats)
        return cls(frozen, int(character_id), str(dataset_id), names, weights, mains, zeros,
                   frozen.max_theoretical_weight(weights, zero_weight_stats=zeros)
                   if max_weight is None else float(max_weight),
                   str(getattr(static_release, "database_path", "")),
                   int(getattr(static_release, "schema_version", 0) or 0),
                   str(getattr(static_release, "sha256", "")))

    def metadata(self) -> dict[str, Any]:
        self.engine.prepare_weight_queries([self.weights] + ([self.main_weights] if self.main_weights is not None else []),
                                           self.names.values())
        rules = json.dumps(asdict(self.engine.stat_catalog), sort_keys=True, ensure_ascii=False)
        return dict(
            character_id=self.character_id, static_dataset_id=self.dataset_id,
            static_database_path=self.static_database_path,
            static_schema_version=self.static_schema_version,
            static_sha256=self.static_sha256,
            weight_source_character_id=base_weight_character_id(self.character_id),
            scoring_method="base_weights", scoring_rule="ScoringEngine-v2",
            rule_sha256=sha256(rules.encode("utf-8")).hexdigest(),
            property_weights={pid: self.engine.flexible_weight(name, dict(self.weights))
                              for pid, name in self.names.items()},
            main_property_weights=(
                {pid: self.engine.flexible_weight(name, dict(self.main_weights))
                 for pid, name in self.names.items()} if self.main_weights is not None else None
            ),
            scoring_weights=dict(self.weights),
            scoring_main_weights=dict(self.main_weights) if self.main_weights is not None else None,
            zero_weight_stats=list(self.zero_weight_stats), max_weight=self.max_weight,
        )

    def _inventory_score_input(self, item: Mapping[str, Any], *, main_value=None):
        quality = {"orange": "Gold", "gold": "Gold", "purple": "Purple", "blue": "Blue"}.get(
            str(item.get("quality") or "").casefold())
        if quality is None:
            raise ValueError("装备品质资料不足")
        stats = tuple(item.get("sub_stats") or ())
        mains = tuple(item.get("main_stats") or ())
        if any(str(stat.get("property_id") or "") not in self.names for stat in (*stats, *mains)):
            raise ValueError("装备属性映射资料不足")
        sub_stats = {self.names[str(stat["property_id"])]: legacy_stat_value(stat.get("value"), bool(stat.get("percent")))
                     for stat in stats}
        if item.get("kind") == "module":
            area = int(item.get("grid_count") or grid_count_from_geometry(item.get("geometry")))
            if area <= 0:
                raise ValueError("驱动格数资料不足")
            return SimpleNamespace(item_type="drive", sub_stats=sub_stats, area=area, quality=quality)
        if item.get("kind") != "core" or not mains:
            raise ValueError("卡带主词条资料不足")
        if main_value is not None and not isfinite(float(main_value)):
            raise ValueError("卡带主词条数值资料不足")
        # 缺历史满级主词条值时仍由公共评分器按目录与品质回退，不拿低等级快照代替。
        return SimpleNamespace(item_type="tape", area=15, sub_stats=sub_stats, quality=quality,
            main_stats=self.names[str(mains[0]["property_id"])], main_value=main_value)

    def score_inventory_item(self, item: Mapping[str, Any], *, main_value=None) -> float:
        if item.get("virtual"):
            return 0.0
        value = self._inventory_score_input(item, main_value=main_value)
        if value.item_type == "drive":
            return float(self.engine.calculate_drive_score(value, dict(self.weights), self.max_weight,
                zero_weight_stats=self.zero_weight_stats))
        return float(self.engine.calculate_cartridge_score(value, dict(self.weights), self.max_weight,
            dict(self.main_weights) if self.main_weights is not None else None,
            zero_weight_stats=self.zero_weight_stats))

    def score_inventory_items(self, entries, *, checkpoint=lambda: None):
        """整批只改本地评分投影；缺资料保留未知，组件错误和取消仍上抛。"""
        return score_comparison_batches([(self, entries)], checkpoint=checkpoint)[0]

    def verify_result_score(self, snapshot):
        """评分口径不一致时保留未知，不混入当前比较结果。"""
        if snapshot.get("virtual"):
            expected = 0.0
        elif snapshot.get("type") == "tape":
            expected = self.engine.calculate_cartridge_score(
                SimpleNamespace(main_stats=snapshot.get("main_stats", ""),
                                main_value=snapshot.get("main_value"),
                                sub_stats=snapshot.get("sub_stats", {}), quality=snapshot.get("quality", "Gold")),
                dict(self.weights), self.max_weight,
                dict(self.main_weights) if self.main_weights is not None else None,
                zero_weight_stats=self.zero_weight_stats,
            )
        else:
            expected = self.engine.calculate_drive_score(
                SimpleNamespace(sub_stats=snapshot.get("sub_stats", {}), area=snapshot.get("area", 0),
                                quality=snapshot.get("quality", "Gold")),
                dict(self.weights), self.max_weight, zero_weight_stats=self.zero_weight_stats,
            )
        score = snapshot.get("score")
        if score is None or not isfinite(float(score)) or abs(float(score) - expected) > .011:
            snapshot.pop("score", None)
            snapshot.pop("grade", None)
            snapshot["comparison_score_unavailable"] = "评分口径与本次计算不一致"
        return snapshot


def freeze_comparison_scorers(engine, specifications, *, attributes, dataset_id,
                              static_release=None, checkpoint=lambda: None):
    """同一请求共享规则副本，角色及主词条 profile 按原顺序成批送分析组件。"""
    specifications = list(specifications)
    if not specifications:
        return []
    checkpoint()
    frozen = engine.frozen_copy()
    profiles, primary_indices = [], []
    for spec in specifications:
        checkpoint()
        primary_indices.append(len(profiles))
        profiles.append(dict(key=str(len(profiles)), weights=dict(spec["weights"]),
                             main_weights=spec.get("main_weights"),
                             zero_weight_stats=list(spec.get("zero_weight_stats") or ())))
        if spec.get("main_weights") is not None:
            profiles.append(dict(key=str(len(profiles)), weights=dict(spec["main_weights"])))
    names = [legacy_stat_name(str(row["attribute_id"])) or ScoringEngine._scoring_property_name(row)
             for row in attributes]
    maximums = frozen.prepare_score_profiles(profiles, queries=names, checkpoint=checkpoint)
    return [FrozenComparisonScorer.from_engine(
        engine, **spec, attributes=attributes, dataset_id=dataset_id,
        frozen_engine=frozen, max_weight=maximums[index], static_release=static_release,
    ) for spec, index in zip(specifications, primary_indices)]


def score_comparison_batches(batches, *, checkpoint=lambda: None):
    """有界跨角色矩阵只散回所属 profile 的结果；未知、虚拟件和失败语义不变。"""
    batches = list(batches)
    if not batches:
        return []
    engine = batches[0][0].engine
    scores, group, item_count = [], [], 0

    def flush():
        if not group:
            return
        values = [value for _, entries in group for _, value in entries]
        profiles = [dict(key=str(index), weights=dict(batches[owner][0].weights),
                         main_weights=batches[owner][0].main_weights,
                         max_weight=batches[owner][0].max_weight,
                         zero_weight_stats=list(batches[owner][0].zero_weight_stats))
                    for index, (owner, _) in enumerate(group)]
        engine.prepare_scores(values, profiles=profiles, assign_scores=True,
                              cache_items=False, checkpoint=checkpoint)
        for profile_index, (owner, entries) in enumerate(group):
            for index, value in entries:
                checkpoint()
                scores[owner][index] = float(value.role_scores[str(profile_index)])
        group.clear()

    for owner, (scorer, entries) in enumerate(batches):
        if scorer.engine is not engine:
            raise ValueError("比较批次必须属于同一冻结评分器")
        prepared, row_scores = [], []
        for index, (item, main_value) in enumerate(entries):
            checkpoint()
            row_scores.append(None)
            if item.get("virtual"):
                row_scores[index] = 0.0
                continue
            try:
                value = scorer._inventory_score_input(item, main_value=main_value)
            except (ValueError, TypeError, KeyError):
                continue
            prepared.append((index, value))
        scores.append(row_scores)
        # 小矩阵立即消费，不用 N×R 无界缓存换取少量进程启动。
        for offset in range(0, len(prepared), 4096):
            chunk = prepared[offset:offset + 4096]
            if group and (len(group) >= 128 or (item_count + len(chunk)) * (len(group) + 1) > 4096):
                flush()
                item_count = 0
            group.append((owner, chunk))
            item_count += len(chunk)
    flush()
    checkpoint()
    return scores


def persist_comparison_diff(diff: Mapping[str, Any] | None) -> dict[str, Any]:
    result = deepcopy(dict(diff or {}))
    result[DIFF_CHANGED] = bool(result.get(DIFF_CHANGED))
    result[DIFF_ADDED_UIDS] = sorted(str(uid) for uid in result.get(DIFF_ADDED_UIDS, ()) if uid)
    for key in (DIFF_ADDED, DIFF_REMOVED):
        result[key] = [dict(item) for item in result.get(key, ()) if isinstance(item, Mapping)]
    return result


def comparison_notice(diff: Mapping[str, Any]) -> str:
    if diff.get("comparison_version") == 1 and diff.get("score_basis") == "calculation_weights":
        if any(item.get("comparison_score_unavailable")
               for key in (DIFF_ADDED, DIFF_REMOVED) for item in diff.get(key, ())):
            return "新旧装备按本次计算权重对比；部分装备评分资料不足。"
        return "新旧装备评分均按本次计算权重对比。"
    return "历史变动使用当时保存评分；重新计算并保存后可统一对比。"


def comparison_display_item(item: Mapping[str, Any], diff: Mapping[str, Any]) -> dict[str, Any]:
    """展示水合不拿实时权重补造缺失的历史评分。"""
    result = deepcopy(dict(item))
    if result.get("score") is None:
        result["comparison_score_unavailable"] = "评分资料不足"
    if diff.get("comparison_version") == 1 and diff.get("score_basis") == "calculation_weights":
        scoring = diff.get("scoring") or {}
        result["_comparison_weights"] = dict(scoring.get("scoring_weights") or {})
        result["_comparison_main_weights"] = scoring.get("scoring_main_weights")
    return result
