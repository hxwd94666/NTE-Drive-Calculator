# 编排装备鉴定所需的解析、评分与角色适配。
"""Match one equipment item against account character blueprints."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from src.integrations.vision.recognition.resources import RecognitionResources, RecognitionResourceError
from src.services.recognition.resources import load_recognition_resources, check_recognition_resources
from src.models.equipment import Drive, Tape
from src.optimizer.scoring import ScoringEngine
from src.solver.orchestrator import NTEPipelineOrchestrator
from src.domain.equipment.matching import EquipmentMatchContext
from src.services.identification.matching_context import build_equipment_match_context
from src.services.identification.matching_settings import MatchingSettingsService


class EquipmentIdentificationService:
    """Qt-free scoring boundary shared by identification and warehouse."""

    def __init__(
        self,
        orchestrator: NTEPipelineOrchestrator,
        context: EquipmentMatchContext,
        scoring_engine: ScoringEngine,
    ) -> None:
        self._orchestrator = orchestrator
        self.context = context
        self._scoring = scoring_engine

    @property
    def scoring(self) -> ScoringEngine:
        """本批与图纸共用角色数据的评分器；调用方不另读可变账号权重。"""
        return self._scoring

    @classmethod
    def from_paths(
        cls,
        *,
        config_dir: str | Path,
        user_database_path: str | Path,
        static_database_path: str | Path | None = None,
        cancel_check: Callable[[], bool] | None = None,
        resources: RecognitionResources | None = None,
    ) -> "EquipmentIdentificationService":
        """Build a matcher from paths frozen at operation start."""
        resources = resources or load_recognition_resources(config_dir, static_database_path)
        if Path(config_dir).resolve() != resources.config_dir or (static_database_path is not None
                and Path(static_database_path).resolve() != resources.static_database_path):
            raise RecognitionResourceError("鉴定路径与本批识别资料不一致。")
        check_recognition_resources(resources)
        catalog = resources.stat_catalog()
        orchestrator = NTEPipelineOrchestrator(
            config_dir=str(config_dir),
            user_database_path=Path(user_database_path),
            static_database_path=resources.static_database_path,
            expected_dataset=resources.dataset, stat_catalog=catalog,
        )
        settings = MatchingSettingsService(user_database_path).load()
        context = build_equipment_match_context(orchestrator, settings, cancel_check=cancel_check)
        scoring = ScoringEngine(
            str(config_dir),
            user_database_path=Path(user_database_path),
            # 图纸与评分消费同一批角色权重，避免再次读取可变账号配置。
            roles_db=orchestrator.roles_db,
            static_database_path=resources.static_database_path, stat_catalog=catalog,
        )
        check_recognition_resources(resources)
        MatchingSettingsService(user_database_path).require_identity(context.settings_identity)
        return cls(orchestrator, context, scoring)

    def identify_item(self, item: Drive | Tape) -> dict[str, Any]:
        """Return ranked character matches for one drive or tape."""
        return self.identify_items([item])[0]

    def identify_items(self, items, *, checkpoint=None):
        items = [item.model_copy(deep=True) for item in items]
        self._scoring.prepare_scores(items, checkpoint=checkpoint, assign_scores=True)
        results = []
        for item in items:
            if checkpoint is not None:
                checkpoint()
            results.append(self._identify_scored_item(item))
        return results

    def _identify_scored_item(self, item):
        orchestrator = self._orchestrator
        scoring = self._scoring
        rows: list[dict[str, Any]] = []
        if isinstance(item, Tape):
            item.set_name = orchestrator._resolve_set_name(item.set_name)
        matches_by_role = {}
        for match in self.context.for_item(item):
            matches_by_role.setdefault(match.role, []).append(match)
        for role_name, role_data in orchestrator.roles_db.items():
            matches = matches_by_role.get(role_name, [])
            if not matches:
                continue
            target_set = matches[0].suit_name
            weights = role_data.get("weights", {})
            main_weights = (
                role_data.get("main_weights")
                if isinstance(role_data, dict)
                else None
            )
            score = item.role_scores[role_name]
            if isinstance(item, Tape):
                match_desc = "默认匹配" if any(match.is_default for match in matches) else "自选匹配"
                area = 15
            else:
                in_set = any(item.shape_id in match.set_shapes for match in matches)
                in_extra = any(item.shape_id in match.extra_shapes for match in matches)
                match_desc = ("默认匹配" if any(match.is_default for match in matches) else "自选匹配")
                match_desc += " · " + ("套装位 / 散件位" if in_set and in_extra else "套装位" if in_set else "散件位")
                area = item.area
            grade = scoring.get_grade_tag(score, area)
            max_score = area * 10.0
            rows.append(
                {
                    "role": role_name,
                    "set": target_set,
                    "score": score,
                    "grade": grade,
                    "percent": round(score / max_score * 100, 1)
                    if max_score
                    else 0,
                    "match": match_desc,
                    "weights": weights,
                    "main_weights": main_weights,
                    "matched_suits": [{"suit_id": match.suit_id, "name": match.suit_name,
                                       "is_default": match.is_default,
                                       # 一张图纸可同时容纳套装位与散件位，不把另一种适配丢掉。
                                       "positions": (["cartridge"] if isinstance(item, Tape) else
                                           [key for key, shapes in (("set", match.set_shapes), ("extra", match.extra_shapes))
                                            if item.shape_id in shapes])} for match in matches],
                }
            )
        rows.sort(key=lambda row: row["score"], reverse=True)
        errors = self.context.errors + tuple((str(identity), "", "角色资料缺失")
                                             for identity in self.context.missing_character_ids)
        return {"item": item, "rows": rows, "matching_errors": errors,
                "matching_status": "incomplete" if errors else "matched" if rows else "unmatched",
                "matching_settings_identity": self.context.settings_identity}
