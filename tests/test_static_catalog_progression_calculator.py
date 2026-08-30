# 验证公共养成计算器的 Qt 无关请求适配与状态合并。
"""Qt-free tests for the static-catalog progression calculator contract."""

from __future__ import annotations

import unittest
from dataclasses import dataclass
from pathlib import Path

from src.domain.progression_stamina import (
    FarmingStage,
    MaterialRequirement,
    MaterialYield,
    StaminaPlanStatus,
)
from src.domain.static_catalog_terminology import LocalizedTermRecord
from src.features.static_catalog.progression_calculator_models import (
    CALLBACK_ERROR_TEXT,
    ProgressionCalculatorOrchestrator,
    deliver_progression_outcome,
)
from src.services.progression_stamina_service import ProgressionStaminaService
from src.services.static_catalog_fork_release_metadata import (
    ForkProgressionMaterialRequirement,
    ForkProgressionRequest,
    ForkProgressionRequirementGap,
    ForkProgressionState,
)
from src.services.static_catalog_terminology_service import (
    StaticCatalogTerminologyService,
)


ROOT = Path(__file__).resolve().parents[1]


class _TerminologySource:
    def lookup_localized_term(
        self,
        entity_kind: str,
        stable_id: str,
        *,
        context: str | None,
    ) -> LocalizedTermRecord | None:
        if entity_kind != "item":
            return None
        if stable_id == "gold" and context == "progression_cost":
            stable_id = "Fons"
        names = {
            "Fons": "方斯",
            "known": "定向材料",
            "unknown": "未命名测试材料",
        }
        name = names.get(stable_id)
        if name is None:
            return None
        return LocalizedTermRecord(
            entity_kind="item",
            canonical_id=stable_id,
            names={"zh-CN": name},
            text_table="ItemText",
            text_key=f"item_{stable_id}_name",
        )


class _StageSource:
    def list_progression_farming_stages(self) -> tuple[FarmingStage, ...]:
        return (
            FarmingStage(
                stage_id="stage_formal",
                label="正式材料副本",
                minimum_hunter_level=10,
                minimum_identification_level=1,
                stamina_cost=20,
                yields=(
                    MaterialYield("Fons", 10),
                    MaterialYield("known", 2),
                    MaterialYield("unknown", 1),
                ),
                source="release_static_v30",
            ),
        )


@dataclass(frozen=True, slots=True)
class _Gap:
    reason_code: str
    item_id: str | None = None


class StaticCatalogProgressionCalculatorTests(unittest.TestCase):
    def setUp(self) -> None:
        terminology = StaticCatalogTerminologyService(_TerminologySource())
        self.orchestrator = ProgressionCalculatorOrchestrator(
            service=ProgressionStaminaService(
                official_stage_source=_StageSource(),
            ),
            terminology_service=terminology,
        )

    def test_character_request_merges_exact_aliases_and_keeps_identity(self) -> None:
        session = self.orchestrator.prepare({
            "kind": "skill",
            "character_id": 1072,
            "skill_id": "skill-a",
            "requirements": (
                MaterialRequirement("gold", 15),
                MaterialRequirement("Fons", 5),
            ),
            "requirement_status": "complete",
            "requirement_gaps": (),
        })

        self.assertEqual(session.kind, "skill")
        self.assertEqual(session.entity_id, "1072:skill-a")
        self.assertEqual(session.owner_id, "1072")
        self.assertEqual(session.skill_id, "skill-a")
        self.assertEqual(len(session.materials), 1)
        material = session.materials[0]
        self.assertEqual(material.display_name, "方斯")
        self.assertEqual(material.canonical_id, "Fons")
        self.assertEqual(material.required_quantity, 20)
        self.assertEqual(material.requested_ids, ("gold", "Fons"))
        self.assertNotIn("gold", material.display_name)
        self.assertIn(("规范材料 ID", "Fons"), material.more_info)

        outcome = self.orchestrator.calculate(
            session,
            hunter_level=60,
            effective_identification_level=7,
            owned_quantities={"Fons": 5},
        )
        self.assertEqual(outcome.result.status, StaminaPlanStatus.COMPLETE)
        self.assertEqual(outcome.owner_id, "1072")
        self.assertEqual(outcome.skill_id, "skill-a")
        self.assertEqual(outcome.result.total_stamina, 40)
        self.assertEqual(outcome.result.runs[0].runs, 2)

    def test_character_partial_never_becomes_complete(self) -> None:
        session = self.orchestrator.prepare({
            "kind": "skill",
            "character_id": 1072,
            "skill_id": "skill-a",
            "requirements": (MaterialRequirement("known", 4),),
            "requirement_status": "partial",
            "requirement_gaps": (_Gap("skill_cost_quantity_hidden", "unknown"),),
        })

        outcome = self.orchestrator.calculate(
            session,
            hunter_level=60,
            effective_identification_level=7,
            owned_quantities={},
        )

        self.assertEqual(outcome.result.status, StaminaPlanStatus.PARTIAL)
        self.assertEqual(outcome.result.known_stamina, 40)
        self.assertIsNone(outcome.result.total_stamina)
        self.assertIn("upstream:skill_cost_quantity_hidden", outcome.result.gaps)

    def test_character_unavailable_empty_request_stays_unavailable(self) -> None:
        session = self.orchestrator.prepare({
            "kind": "character_level",
            "character_id": 1072,
            "requirements": (),
            "requirement_status": "unavailable",
            "requirement_gaps": (_Gap("character_level_cost_unavailable"),),
        })

        self.assertEqual(session.owner_id, "1072")
        self.assertIsNone(session.skill_id)

        outcome = self.orchestrator.calculate(
            session,
            hunter_level=40,
            effective_identification_level=4,
            owned_quantities={},
        )

        self.assertEqual(outcome.result.status, StaminaPlanStatus.UNAVAILABLE)
        self.assertEqual(outcome.owner_id, "1072")
        self.assertIsNone(outcome.skill_id)
        self.assertIsNone(outcome.result.total_stamina)
        self.assertEqual(outcome.result.known_stamina, 0)
        self.assertIn("upstream:character_level_cost_unavailable", outcome.result.gaps)

    def test_fork_unknown_total_keeps_known_part_and_gap(self) -> None:
        request = ForkProgressionRequest(
            kind="fork_progression",
            fork_id="fork_test",
            current=ForkProgressionState(1, None, 1),
            target=ForkProgressionState(20, 1, 2),
            requirements=(
                ForkProgressionMaterialRequirement(
                    item_id="known",
                    required_quantity=4,
                    known_quantity=4,
                    source_refs=("breakthrough:1",),
                ),
                ForkProgressionMaterialRequirement(
                    item_id="unknown",
                    required_quantity=None,
                    known_quantity=2,
                    source_refs=("mixing:2",),
                ),
            ),
            requirement_gaps=(
                ForkProgressionRequirementGap(
                    code="official_quantity_unavailable",
                    source_ref="mixing:2",
                    item_id="unknown",
                ),
            ),
            required_upgrade_exp=None,
        )

        session = self.orchestrator.prepare(request)
        unknown = next(item for item in session.materials if item.key == "unknown")
        self.assertIsNone(unknown.required_quantity)
        self.assertEqual(unknown.known_quantity, 2)
        self.assertEqual(unknown.requirement_text, "完整需求量不可用 · 已知至少 2")

        outcome = self.orchestrator.calculate(
            session,
            hunter_level=60,
            effective_identification_level=7,
            owned_quantities={"known": 2, "unknown": 0},
        )
        self.assertEqual(outcome.kind, "fork_progression")
        self.assertEqual(outcome.entity_id, "fork_test")
        self.assertEqual(outcome.owner_id, "fork_test")
        self.assertIsNone(outcome.skill_id)
        self.assertEqual(outcome.result.status, StaminaPlanStatus.PARTIAL)
        self.assertGreater(outcome.result.known_stamina, 0)
        self.assertIsNone(outcome.result.total_stamina)
        self.assertIn("upstream:official_quantity_unavailable", outcome.result.gaps)

    def test_identification_projection_exposes_only_the_formal_one_level_drop(self) -> None:
        native = self.orchestrator.identification_level(40)
        lowered = self.orchestrator.identification_level(40, effective_level=3)
        self.assertEqual((native.native_level, native.effective_level), (4, 4))
        self.assertEqual(lowered.effective_level, 3)
        self.assertTrue(lowered.lowered)
        with self.assertRaises(ValueError):
            self.orchestrator.identification_level(40, effective_level=2)

    def test_complete_request_with_gaps_is_downgraded_before_calculation(self) -> None:
        session = self.orchestrator.prepare({
            "kind": "skill",
            "character_id": 1072,
            "skill_id": "skill-a",
            "requirements": (MaterialRequirement("known", 2),),
            "requirement_status": "complete",
            "requirement_gaps": (_Gap("source_row_incomplete"),),
        })
        self.assertEqual(session.upstream_status, StaminaPlanStatus.PARTIAL)

        outcome = self.orchestrator.calculate(
            session,
            hunter_level=60,
            effective_identification_level=7,
            owned_quantities={},
        )
        self.assertEqual(outcome.result.status, StaminaPlanStatus.PARTIAL)
        self.assertIsNone(outcome.result.total_stamina)
        self.assertIn("upstream:source_row_incomplete", outcome.result.gaps)

        empty_session = self.orchestrator.prepare({
            "kind": "character_level",
            "character_id": 1072,
            "requirements": (),
            "requirement_status": "complete",
            "requirement_gaps": (_Gap("source_row_missing"),),
        })
        self.assertEqual(
            empty_session.upstream_status,
            StaminaPlanStatus.UNAVAILABLE,
        )

    def test_projection_callback_failure_is_contained_without_qt(self) -> None:
        session = self.orchestrator.prepare({
            "kind": "character_level",
            "character_id": 1072,
            "requirements": (),
            "requirement_status": "complete",
            "requirement_gaps": (),
        })
        outcome = self.orchestrator.calculate(
            session,
            hunter_level=60,
            effective_identification_level=7,
            owned_quantities={},
        )

        def broken_callback(_outcome: object) -> None:
            raise RuntimeError("page was disposed")

        with self.assertLogs(
            "src.features.static_catalog.progression_calculator_models",
            level="ERROR",
        ) as logs:
            error = deliver_progression_outcome(broken_callback, outcome)
        self.assertEqual(error, CALLBACK_ERROR_TEXT)
        self.assertIn("result projection failed", logs.output[0])

    def test_projection_callback_receives_frozen_owner_fields(self) -> None:
        session = self.orchestrator.prepare({
            "kind": "skill",
            "character_id": 1072,
            "skill_id": "skill-a",
            "requirements": (),
            "requirement_status": "complete",
            "requirement_gaps": (),
        })
        outcome = self.orchestrator.calculate(
            session,
            hunter_level=60,
            effective_identification_level=7,
            owned_quantities={},
        )
        received = []

        error = deliver_progression_outcome(received.append, outcome)

        self.assertIsNone(error)
        self.assertEqual(received[0].owner_id, "1072")
        self.assertEqual(received[0].skill_id, "skill-a")

    def test_rejects_unknown_or_malformed_page_requests(self) -> None:
        with self.assertRaises(TypeError):
            self.orchestrator.prepare(object())  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            self.orchestrator.prepare({
                "kind": "skill",
                "character_id": 1072,
                "requirements": (),
                "requirement_status": "complete",
            })
        with self.assertRaises(ValueError):
            self.orchestrator.prepare({
                "kind": "character_level",
                "character_id": 1072,
                "requirements": (MaterialRequirement("known", -1),),
                "requirement_status": "complete",
            })

    def test_qt_layer_is_card_based_and_has_no_private_formal_names(self) -> None:
        source = (
            ROOT / "src" / "features" / "static_catalog" /
            "progression_calculator.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("QTableWidget", source)
        self.assertNotIn("QTableView", source)
        self.assertNotIn("方斯", source)
        self.assertNotIn("甲硬币", source)
        self.assertIn("StaticCatalogTerminologyService", source)
        self.assertIn("更多信息", source)
        self.assertIn("fit_dialog_to_available_screen", source)
        self.assertIn("WorkerThread(", source)
        self.assertIn("freeze_calculation(", source)
        self.assertNotIn("self._orchestrator.calculate(", source)
        self.assertNotIn("ProgressionCalculatorController", source)


if __name__ == "__main__":
    unittest.main()
