# 编排装备鉴定所需的解析、评分与角色适配。
"""Match one equipment item against account character blueprints."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from src.models.equipment import Drive, Tape
from src.optimizer.scoring import ScoringEngine
from src.solver.orchestrator import NTEPipelineOrchestrator


class EquipmentIdentificationService:
    """Qt-free scoring boundary shared by identification and warehouse."""

    def __init__(
        self,
        orchestrator: NTEPipelineOrchestrator,
        blueprints: dict[str, Any],
        scoring_engine: ScoringEngine,
    ) -> None:
        self._orchestrator = orchestrator
        self._blueprints = blueprints
        self._scoring = scoring_engine

    @classmethod
    def from_paths(
        cls,
        *,
        config_dir: str | Path,
        user_database_path: str | Path,
    ) -> "EquipmentIdentificationService":
        """Build a matcher from paths frozen at operation start."""
        orchestrator = NTEPipelineOrchestrator(
            config_dir=str(config_dir),
            user_database_path=Path(user_database_path),
        )
        blueprints = orchestrator.solve_blueprints(list(orchestrator.roles_db))
        scoring = ScoringEngine(
            str(config_dir),
            user_database_path=Path(user_database_path),
        )
        return cls(orchestrator, blueprints, scoring)

    def identify_item(self, item: Drive | Tape) -> dict[str, Any]:
        """Return ranked character matches for one drive or tape."""
        orchestrator = self._orchestrator
        scoring = self._scoring
        rows: list[dict[str, Any]] = []
        if isinstance(item, Tape):
            item.set_name = orchestrator._resolve_set_name(item.set_name)
        for role_name, role_data in orchestrator.roles_db.items():
            role_bps = self._blueprints.get(role_name, [])
            if not role_bps:
                continue
            target_set = orchestrator._resolve_set_name(
                role_data.get("default_set", "")
            )
            weights = role_data.get("weights", {})
            main_weights = (
                role_data.get("main_weights")
                if isinstance(role_data, dict)
                else None
            )
            max_weight = scoring.max_theoretical_weight(weights)
            if isinstance(item, Tape):
                if item.set_name != target_set:
                    continue
                score = scoring.calculate_cartridge_score(
                    item,
                    weights,
                    max_weight,
                    main_weights,
                )
                match_desc = "套装匹配"
                area = 15
            else:
                set_shapes = orchestrator.sets_db[target_set]["shapes"]
                in_set = item.shape_id in set_shapes
                in_extra = any(
                    item.shape_id in blueprint.get("extra_pieces", [])
                    for blueprint in role_bps
                )
                if not in_set and not in_extra:
                    continue
                score = scoring.calculate_drive_score(item, weights, max_weight)
                match_desc = "套装位" if in_set else "散件位"
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
                }
            )
        rows.sort(key=lambda row: row["score"], reverse=True)
        return {"item": item, "rows": rows}
