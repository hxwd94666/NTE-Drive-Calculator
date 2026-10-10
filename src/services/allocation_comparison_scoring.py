# 冻结配装对比的评分口径并复用公共评分公式。
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass
from hashlib import sha256
import json
from math import isfinite
from types import SimpleNamespace
from typing import Any, Mapping

from src.domain.recommended_weights import base_weight_character_id
from src.optimizer.contracts import DIFF_ADDED, DIFF_ADDED_UIDS, DIFF_CHANGED, DIFF_REMOVED
from src.optimizer.scoring import ScoringEngine
from src.services.sqlite_allocation_inventory import legacy_stat_name, legacy_stat_value
from src.services.virtual_equipment_service import grid_count_from_geometry


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

    @classmethod
    def from_engine(cls, engine, *, character_id, weights, main_weights, attributes,
                    dataset_id, zero_weight_stats=()):
        frozen = deepcopy(engine)
        frozen.roles_db = {}
        names = {
            str(row["attribute_id"]): legacy_stat_name(str(row["attribute_id"]))
            or ScoringEngine._scoring_property_name(row)
            for row in attributes
        }
        weights = deepcopy(dict(weights))
        mains = deepcopy(dict(main_weights)) if main_weights is not None else None
        zeros = tuple(str(name) for name in zero_weight_stats)
        return cls(frozen, int(character_id), str(dataset_id), names, weights, mains, zeros,
                   frozen.max_theoretical_weight(weights, zero_weight_stats=zeros))

    def metadata(self) -> dict[str, Any]:
        rules = json.dumps(asdict(self.engine.stat_catalog), sort_keys=True, ensure_ascii=False)
        return dict(
            character_id=self.character_id, static_dataset_id=self.dataset_id,
            weight_source_character_id=base_weight_character_id(self.character_id),
            scoring_method="base_weights", scoring_rule="ScoringEngine-v1",
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

    def score_inventory_item(self, item: Mapping[str, Any], *, main_value=None) -> float:
        if item.get("virtual"):
            return 0.0
        quality = {"orange": "Gold", "gold": "Gold", "purple": "Purple", "blue": "Blue"}.get(
            str(item.get("quality") or "").casefold(),
        )
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
            return float(self.engine.calculate_drive_score(
                SimpleNamespace(sub_stats=sub_stats, area=area, quality=quality),
                dict(self.weights), self.max_weight, zero_weight_stats=self.zero_weight_stats,
            ))
        if item.get("kind") != "core" or not mains:
            raise ValueError("卡带主词条资料不足")
        name = self.names[str(mains[0]["property_id"])]
        # Without a saved full-level value, the public scorer uses its catalogue
        # full-level value and quality coefficient, not the snapshot's low level.
        if main_value is not None and not isfinite(float(main_value)):
            raise ValueError("卡带主词条数值资料不足")
        return float(self.engine.calculate_cartridge_score(
            SimpleNamespace(main_stats=name, main_value=main_value, sub_stats=sub_stats, quality=quality),
            dict(self.weights), self.max_weight,
            dict(self.main_weights) if self.main_weights is not None else None,
        ))

    def verify_result_score(self, snapshot):
        """Mark a different score policy as unknown instead of silently mixing it."""
        if snapshot.get("virtual"):
            expected = 0.0
        elif snapshot.get("type") == "tape":
            expected = self.engine.calculate_cartridge_score(
                SimpleNamespace(main_stats=snapshot.get("main_stats", ""),
                                main_value=snapshot.get("main_value"),
                                sub_stats=snapshot.get("sub_stats", {}), quality=snapshot.get("quality", "Gold")),
                dict(self.weights), self.max_weight,
                dict(self.main_weights) if self.main_weights is not None else None,
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
    """Prevent hydration from inventing missing historical scores from live weights."""
    result = deepcopy(dict(item))
    if result.get("score") is None:
        result["comparison_score_unavailable"] = "评分资料不足"
    if diff.get("comparison_version") == 1 and diff.get("score_basis") == "calculation_weights":
        scoring = diff.get("scoring") or {}
        result["_comparison_weights"] = dict(scoring.get("scoring_weights") or {})
        result["_comparison_main_weights"] = scoring.get("scoring_main_weights")
    return result
