# 测试 v30 正式掉落闭包到公共体力计算的只读接线。
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from src.domain.progression_stamina import (
    FarmingStage,
    MaterialRequirement,
    MaterialYield,
    ProgressionStaminaRequest,
    StaminaPlanStatus,
)
from src.services.progression_stamina_service import ProgressionStaminaService
from src.storage.sqlite.static_game_data_dao import StaticGameDataDao


class ProgressionStaminaStaticSourceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temporary.name) / "candidate-v30.sqlite3"
        with closing(sqlite3.connect(self.database_path)) as connection:
            connection.executescript(
                """
                CREATE TABLE schema_migration (
                    version INTEGER PRIMARY KEY,
                    applied_at_utc TEXT NOT NULL
                );
                INSERT INTO schema_migration VALUES (30, '2026-08-30');
                CREATE TABLE clone_activity (
                    clone_id TEXT PRIMARY KEY,
                    name_zh TEXT NOT NULL
                );
                CREATE TABLE clone_activity_difficulty (
                    clone_id TEXT NOT NULL,
                    difficulty_ordinal INTEGER NOT NULL,
                    difficulty_level INTEGER NOT NULL,
                    team_level INTEGER NOT NULL,
                    stamina_cost INTEGER NOT NULL,
                    drop_id TEXT,
                    PRIMARY KEY (clone_id, difficulty_ordinal)
                );
                CREATE TABLE clone_drop_projection (
                    drop_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    source_kind TEXT NOT NULL,
                    reason_code TEXT
                );
                CREATE TABLE clone_drop_projection_item (
                    drop_id TEXT NOT NULL,
                    item_id TEXT NOT NULL,
                    quantity INTEGER NOT NULL,
                    PRIMARY KEY (drop_id, item_id)
                );
                CREATE TABLE clone_drop_projection_gap (
                    drop_id TEXT NOT NULL,
                    ordinal INTEGER NOT NULL,
                    sequence_id TEXT,
                    reason_code TEXT NOT NULL,
                    source_row_id INTEGER,
                    PRIMARY KEY (drop_id, ordinal)
                );
                """
            )
            connection.executemany(
                "INSERT INTO clone_activity VALUES (?, ?)",
                (("formal", "正式材料本"), ("gaps", "缺口材料本")),
            )
            connection.executemany(
                "INSERT INTO clone_activity_difficulty VALUES (?,?,?,?,?,?)",
                (
                    ("formal", 0, 3, 30, 40, "drop_name_missing"),
                    ("formal", 1, 4, 40, 40, "drop_complete"),
                    ("gaps", 0, 3, 30, 40, "drop_group_missing"),
                    ("gaps", 1, 4, 40, 40, "drop_divergent"),
                ),
            )
            connection.executemany(
                "INSERT INTO clone_drop_projection VALUES (?,?,?,?)",
                (
                    (
                        "drop_name_missing",
                        "partial",
                        "official_drop_closure",
                        "name_missing",
                    ),
                    ("drop_complete", "complete", "official_drop_closure", None),
                    (
                        "drop_group_missing",
                        "unavailable",
                        "official_drop_closure",
                        "drop_group_missing",
                    ),
                    (
                        "drop_divergent",
                        "partial",
                        "official_drop_closure",
                        "sequence_branch_divergent",
                    ),
                ),
            )
            connection.executemany(
                "INSERT INTO clone_drop_projection_item VALUES (?,?,?)",
                (
                    ("drop_name_missing", "material_a", 2),
                    ("drop_name_missing", "material_b", 1),
                    ("drop_complete", "material_a", 4),
                    ("drop_complete", "material_b", 2),
                    ("drop_divergent", "fabricated_material", 99),
                ),
            )
            connection.executemany(
                "INSERT INTO clone_drop_projection_gap VALUES (?,?,?,?,NULL)",
                (
                    ("drop_name_missing", 0, None, "name_missing"),
                    ("drop_group_missing", 0, None, "drop_group_missing"),
                    (
                        "drop_divergent",
                        0,
                        "sequence-random",
                        "sequence_branch_divergent",
                    ),
                ),
            )
            connection.commit()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @staticmethod
    def _requirements(*item_ids: str) -> tuple[MaterialRequirement, ...]:
        return tuple(
            MaterialRequirement(item_id, 4 if item_id == "material_a" else 2)
            for item_id in item_ids
        )

    def test_v30_query_uses_exact_items_even_when_only_name_is_missing(self) -> None:
        with StaticGameDataDao(self.database_path) as dao:
            stages = dao.list_progression_farming_stages()

        self.assertEqual(
            ("formal:0", "formal:1"),
            tuple(stage.stage_id for stage in stages),
        )
        self.assertEqual(
            (("material_a", 2), ("material_b", 1)),
            tuple((item.item_id, item.quantity) for item in stages[0].yields),
        )
        self.assertEqual(
            (30, 3),
            (
                stages[0].minimum_hunter_level,
                stages[0].minimum_identification_level,
            ),
        )
        self.assertTrue(all(stage.stamina_cost == 40 for stage in stages))
        self.assertTrue(
            all(stage.source == "official_static_drop_projection_v30" for stage in stages)
        )

    def test_formal_source_counts_multi_material_stage_once_and_respects_lowering(
        self,
    ) -> None:
        with StaticGameDataDao(self.database_path) as dao:
            service = ProgressionStaminaService(official_stage_source=dao)
            native = service.calculate(ProgressionStaminaRequest(
                hunter_level=40,
                requirements=self._requirements("material_a", "material_b"),
                stages=(),
            ))
            lowered = service.calculate(ProgressionStaminaRequest(
                hunter_level=40,
                effective_identification_level=3,
                requirements=self._requirements("material_a", "material_b"),
                stages=(),
            ))

        self.assertEqual(native.total_stamina, 40)
        self.assertEqual(native.runs[0].stage_id, "formal:1")
        self.assertEqual(lowered.total_stamina, 80)
        self.assertEqual(lowered.runs[0].stage_id, "formal:0")
        self.assertEqual(lowered.runs[0].runs, 2)
        self.assertTrue(lowered.identification.lowered)

    def test_missing_formal_yield_stays_partial_or_unavailable(self) -> None:
        with StaticGameDataDao(self.database_path) as dao:
            service = ProgressionStaminaService(official_stage_source=dao)
            partial = service.calculate(ProgressionStaminaRequest(
                hunter_level=40,
                requirements=self._requirements(
                    "material_a", "material_b", "unknown_material"
                ),
                stages=(),
            ))
            unavailable = service.calculate(ProgressionStaminaRequest(
                hunter_level=40,
                requirements=(MaterialRequirement("unknown_material", 1),),
                stages=(),
            ))

        self.assertEqual(partial.status, StaminaPlanStatus.PARTIAL)
        self.assertEqual(partial.known_stamina, 40)
        self.assertIsNone(partial.total_stamina)
        self.assertEqual(partial.unresolved_item_ids, ("unknown_material",))
        self.assertEqual(unavailable.status, StaminaPlanStatus.UNAVAILABLE)
        self.assertEqual(unavailable.known_stamina, 0)

    def test_explicit_user_stages_take_priority_over_the_formal_source(self) -> None:
        class UnexpectedOfficialSource:
            def list_progression_farming_stages(self) -> tuple[FarmingStage, ...]:
                raise AssertionError("显式用户档位不应读取正式来源")

        user_stage = FarmingStage(
            stage_id="user-confirmed",
            label="用户确认",
            minimum_hunter_level=1,
            minimum_identification_level=0,
            stamina_cost=7,
            yields=(MaterialYield("unknown_material", 2),),
        )
        result = ProgressionStaminaService(
            official_stage_source=UnexpectedOfficialSource()
        ).calculate(ProgressionStaminaRequest(
            hunter_level=40,
            requirements=(MaterialRequirement("unknown_material", 2),),
            stages=(user_stage,),
        ))

        self.assertEqual(result.status, StaminaPlanStatus.COMPLETE)
        self.assertEqual(result.total_stamina, 7)
        self.assertEqual(result.runs[0].stage_id, "user-confirmed")
        self.assertEqual(result.runs[0].source, "user_supplied")


if __name__ == "__main__":
    unittest.main()
