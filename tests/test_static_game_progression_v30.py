from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from src.services.static_catalog_terminology_service import StaticCatalogTerminologyService
from src.storage.sqlite import static_game_data_dao
from src.storage.sqlite.static_game_data_dao import StaticGameDataDao
from tools.game_data.static_database_build_support import SCHEMA_PATHS
from tools.game_data.static_database_progression_imports import ProgressionImportMixin


class _FixtureBuilder(ProgressionImportMixin):
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection
        self.source_row_ids: dict[tuple[str, str], int] = {}
        self.rows = {
            "character_abilities": {
                "1071": {
                    "CharacterAbilityList": [{
                        "Value": {
                            "LevelsCostItems": [{
                                "CostItems": [
                                    {"ID": "gold", "Number": 2000},
                                    {"ID": "MatA", "Number": 2},
                                ]
                            }]
                        }
                    }]
                }
            },
            "fork_breakthroughs": {
                "Pack_1": {
                    "NeedItems": "MatB:3",
                    "NeedGolds": "gold:16000",
                }
            },
            "character_breakthroughs": {
                "Role_0": {"NeedItems": "0", "NeedGolds": ""},
            },
            "fork_stars": {
                "Star_1": {"NeedGolds": "gold:100"},
            },
            "drop_groups": {
                "drop_exact_0": {
                    "SequenceId": "droplist_MatA",
                    "SequenceWeight": 1.0,
                    "ModifyNum": 3,
                    "DropConditions": [],
                },
                "drop_partial_0": {
                    "SequenceId": "droplist_Random",
                    "SequenceWeight": 1.0,
                    "ModifyNum": 1,
                    "DropConditions": [],
                },
                "drop_currency_0": {
                    "SequenceId": "droplist_gold",
                    "SequenceWeight": 1.0,
                    "ModifyNum": 4000,
                    "DropConditions": [],
                },
            },
            "drop_sequences": {
                "droplist_MatA_0": {
                    "ItemID": "MatA",
                    "SequenceNumType": "EDropSequenceNumType::DROPSEQUENCENUMTYPE_FIXED",
                    "Num": 2,
                    "SequenceProbability": [],
                    "MinNum": 0,
                    "MaxNum": 0,
                    "Formula": "",
                    "Weight": 1,
                    "LimitLevel": "",
                },
                "droplist_Random_0": {
                    "ItemID": "MatB",
                    "SequenceNumType": "EDropSequenceNumType::DROPSEQUENCENUMTYPE_RANGE",
                    "Num": 0,
                    "SequenceProbability": [],
                    "MinNum": 1,
                    "MaxNum": 3,
                    "Formula": "",
                    "Weight": 1,
                    "LimitLevel": "",
                },
            },
            "item_catalog": {
                "MatA": self._item("材料甲", "mat_a"),
                "MatB": self._item("材料乙", "mat_b"),
            },
            "capital_item_catalog": {
                "Fons": self._item("方斯", "item_Fons_name"),
                "Gold": self._item("甲硬币", "gold_name", table="/Game/Text/ST_Ui.ST_Ui"),
            },
            "item_qualities": {
                "ITEM_QUALITY_ORANGE": {
                    "QualityText": {
                        "TableId": "/Game/Text/ST_Common.ST_Common",
                        "Key": "DT_ItemQuality_S",
                        "LocalizedString": "S",
                    },
                    "QualityDesc": {
                        "TableId": "/Game/Text/ST_Ui.ST_Ui",
                        "Key": "Quality_Orange",
                        "LocalizedString": "橙色",
                    },
                }
            },
            "string_item": {
                "mat_a": "材料甲",
                "mat_b": "材料乙",
                "item_Fons_name": "方斯",
            },
            "string_ui": {
                "gold_name": "甲硬币",
                "LimitEditionTag": "限定",
                **{f"campaign_{index}": f"特刊{index}" for index in range(8)},
            },
            "string_ui_j": {"MainActivity_02": "常驻"},
            "string_common": {
                "DT_ItemQuality_S": "S",
                **{
                    key: name
                    for key, name in (
                        ("DamageResistChaos", "暗属性异能伤害抗性"),
                        ("DamageResistCosmos", "光属性异能伤害抗性"),
                        ("DamageResistIncantation", "咒属性异能伤害抗性"),
                        ("DamageResistLakshana", "相属性异能伤害抗性"),
                        ("DamageResistNature", "灵属性异能伤害抗性"),
                        ("DamageResistPsyche", "魂属性异能伤害抗性"),
                        ("DamageResistPsychically", "心灵伤害抗性"),
                    )
                },
            },
            "abyss_clone_levels": {
                "fixture": {
                    "LevelConfigArray": [{
                        "SpawnMonsterConfigMap": [
                            {"Key": "EAbyssFightStage::FirstHalf"},
                            {"Key": "EAbyssFightStage::SecondHalf"},
                        ]
                    }]
                }
            },
        }
        limited_ids = (1010, 1052, 1004, 1071, 1076, 1075, 1036, 1072)
        limited_sources = (
            "lottery_nanali", "lottery_xun", "lottery_anhunqu", "lottery_kaesi",
            "lottery_zhenhong", "lottery_yiluoyi", "lottery_canhong", "lottery_lingke",
        )
        self.rows["lottery_permanent"] = {
            "Properties": {"CharacterIDs": ["1003"]}
        }
        for source_name, character_id in zip(limited_sources, limited_ids, strict=True):
            drop_id = f"drop_limited_{character_id}"
            self.rows[source_name] = {
                "Properties": {
                    "bActivityPool": True,
                    "PoolTypeID": "ECardPoolType::CardPool_Character",
                    "CharacterIDs": [str(character_id)],
                    "ModuleDropDatas": [{
                        "CellClass": "EMonopolyBoardCellType::EMBCT_Character",
                        "DropID": drop_id,
                    }],
                }
            }
            self.rows["drop_groups"][f"{drop_id}_0"] = {
                "SequenceId": f"droplist_{character_id}",
                "SequenceWeight": 1.0,
                "ModifyNum": 1,
                "DropConditions": [],
            }
        fork_ids = (
            "fork_TigerTally", "fork_Time", "fork_Rose", "fork_GoldWool",
            "fork_LunarPhase", "fork_Door", "fork_DemonBlade", "fork_GoldRecord",
        )
        pool_ids = tuple(f"Pool{index}" for index in range(8))
        self.rows["fork_items"] = {fork_id: {} for fork_id in fork_ids}
        self.rows["fork_lottery_data"] = {
            "1": {"PoolIDMap": [
                {"Value": pool_id} for pool_id in pool_ids
            ]}
        }
        self.rows["fork_lottery_pools"] = {
            pool_id: {
                "UpList": [fork_id],
                "ShowText1": {
                    "TableId": "/Game/Text/ST_Ui.ST_Ui",
                    "Key": f"campaign_{index}",
                    "LocalizedString": f"特刊{index}",
                },
            }
            for index, (pool_id, fork_id) in enumerate(zip(pool_ids, fork_ids, strict=True))
        }

    @staticmethod
    def _item(name: str, key: str, *, table: str = "/Game/Text/ST_Item.ST_Item"):
        return {
            "ItemName": {
                "TableId": table,
                "Key": key,
                "LocalizedString": name,
            },
            "ItemQuality": "EItemQuality::ITEM_QUALITY_GREEN",
            "ItemIcon": {"AssetPathName": f"/Game/UI/{key}.{key}"},
        }

    def source_row_id(self, table: str, row_key: str) -> int:
        return self.source_row_ids[(table, str(row_key))]


class StaticGameProgressionV30Test(unittest.TestCase):
    def setUp(self) -> None:
        self.connection = sqlite3.connect(":memory:")
        self.connection.execute("PRAGMA foreign_keys = ON")
        for schema_path in SCHEMA_PATHS:
            self.connection.executescript(schema_path.read_text(encoding="utf-8"))
        self.builder = _FixtureBuilder(self.connection)
        self._insert_sources()
        self._insert_characters()
        self._insert_forks()
        self._insert_clone_difficulties()
        self.connection.commit()

    def tearDown(self) -> None:
        self.connection.close()

    def _source(self, table: str, row_key: str) -> int:
        source_file_id = len(self.builder.source_row_ids) + 1
        source_row_id = source_file_id
        self.connection.execute(
            "INSERT INTO source_file VALUES (?,?,?,1)",
            (source_file_id, f"{table}/{row_key}.json", f"hash-{source_file_id}"),
        )
        self.connection.execute(
            "INSERT INTO source_row VALUES (?,?,?,NULL,?)",
            (source_row_id, source_file_id, row_key, f"row-hash-{source_file_id}"),
        )
        self.builder.source_row_ids[(table, row_key)] = source_row_id
        return source_row_id

    def _insert_sources(self) -> None:
        for table, rows in self.builder.rows.items():
            if table == "drop_sequences":
                continue
            for row_key in rows:
                self._source(table, row_key)
        self.character_sources = {
            character_id: self._source("character", str(character_id))
            for character_id in (
                1003, 1004, 1010, 1036, 1046, 1051,
                1052, 1071, 1072, 1073, 1075, 1076,
            )
        }
        self.clone_source = self._source("clone_system", "clone")

    def _insert_characters(self) -> None:
        rows = []
        for character_id, source_row_id in self.character_sources.items():
            rows.append((
                character_id,
                f"角色{character_id}",
                None,
                None,
                None,
                None,
                f"/Game/{character_id}",
                "2026-06-19T06:00:00" if character_id == 1071 else None,
                source_row_id,
            ))
        self.connection.executemany(
            "INSERT INTO character VALUES (?,?,?,?,?,?,?,?,?)",
            rows,
        )

    def _insert_forks(self) -> None:
        for fork_id in self.builder.rows["fork_items"]:
            self.connection.execute(
                "INSERT INTO fork_item VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    fork_id,
                    fork_id,
                    None,
                    None,
                    None,
                    "ORANGE",
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    "[]",
                    self.builder.source_row_id("fork_items", fork_id),
                ),
            )

    def _insert_clone_difficulties(self) -> None:
        for ordinal, drop_id in enumerate((
            "drop_exact", "drop_currency", "drop_partial", "drop_missing"
        )):
            clone_id = f"clone-{ordinal}"
            self.connection.execute(
                "INSERT INTO clone_activity VALUES (?,?,?,?,?,?,?,?)",
                (clone_id, "Test", None, clone_id, None, 1, 0, self.clone_source),
            )
            self.connection.execute(
                "INSERT INTO clone_activity_difficulty VALUES (?,?,?,?,?,?,?,?)",
                (clone_id, 0, ordinal + 1, 10, 20, drop_id, None, None),
            )

    def test_release_annotation_preserves_provenance_and_official_date_priority(self) -> None:
        self.builder._import_progression_catalog()

        fallback = self.connection.execute(
            """SELECT quality_source_kind, acquisition_source_kind,
                      mainland_release_date, release_source_kind, official_source_row_id
               FROM character_release_annotation WHERE character_id = 1004"""
        ).fetchone()
        official = self.connection.execute(
            """SELECT mainland_release_date, release_source_kind, official_source_row_id
               FROM character_release_annotation WHERE character_id = 1071"""
        ).fetchone()

        self.assertEqual(
            ("reviewed_fallback", "official", "2026-05-28", "official", None),
            fallback,
        )
        self.assertEqual(
            ("2026-06-19", "official", self.character_sources[1071]),
            official,
        )

    def test_item_catalog_keeps_text_identity_and_context_alias(self) -> None:
        self.builder._import_progression_catalog()

        fons = self.connection.execute(
            """SELECT name_zh, name_text_table, name_text_key, source_kind
               FROM progression_item WHERE item_id = 'Fons'"""
        ).fetchone()
        alias = self.connection.execute(
            """SELECT item_id, source_kind FROM progression_item_alias
               WHERE token = 'gold' AND context = 'progression_cost'"""
        ).fetchone()

        self.assertEqual(
            ("方斯", "/Game/Text/ST_Item.ST_Item", "item_Fons_name", "official_item_catalog"),
            fons,
        )
        self.assertEqual(("Fons", "product_contract"), alias)
        self.assertIsNone(self.connection.execute(
            "SELECT item_id FROM progression_item WHERE item_id = '0'"
        ).fetchone())
        quality = self.connection.execute(
            """SELECT grade_zh, grade_text_key, color_zh, color_text_key
               FROM item_quality_term WHERE quality_id = 'ORANGE'"""
        ).fetchone()
        self.assertEqual(
            ("S", "DT_ItemQuality_S", "橙色", "Quality_Orange"),
            quality,
        )

    def test_readonly_dao_resolves_alias_then_exact_case_canonical(self) -> None:
        self.builder._import_progression_catalog()
        self.connection.commit()
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "v30.sqlite3"
            with closing(sqlite3.connect(database_path)) as destination:
                self.connection.backup(destination)
                destination.execute(
                    "INSERT INTO schema_migration VALUES (30, '2026-08-30')"
                )
                destination.execute(
                    "INSERT INTO dataset VALUES ('fixture-v30', 36, '2026-08-30')"
                )
                destination.commit()

            with patch.object(static_game_data_dao, "SCHEMA_VERSION", 30):
                with StaticGameDataDao(database_path) as dao:
                    service = StaticCatalogTerminologyService(dao)
                    cost = service.resolve(
                        "item", "gold", context="progression_cost"
                    )
                    capital = service.resolve(
                        "item", "Gold", context="progression_cost"
                    )
                    grade = service.resolve("item_quality", "ORANGE")
                    color = service.resolve("item_quality_color", "ORANGE")
                    missing_item = service.resolve("item", "NotPresent")
                    missing_effect = service.resolve(
                        "gameplay_effect", "GE_Internal_Only"
                    )

        self.assertEqual(("Fons", "方斯"), (cost.canonical_id, cost.display_name))
        self.assertEqual(
            ("Gold", "甲硬币"),
            (capital.canonical_id, capital.display_name),
        )
        self.assertEqual(("S", "橙色"), (grade.display_name, color.display_name))
        self.assertEqual(
            (("name_missing", None), ("name_missing", None)),
            (
                (missing_item.status, missing_item.display_name),
                (missing_effect.status, missing_effect.display_name),
            ),
        )

    def test_normalized_terms_keep_source_kind_and_relationship_provenance(self) -> None:
        self.builder._import_progression_catalog()
        self.connection.commit()
        normal = self.connection.execute(
            """SELECT source_kind FROM localized_term
               WHERE entity_kind = 'damage_resistance' AND canonical_id = 'normal'"""
        ).fetchone()
        chaos = self.connection.execute(
            """SELECT n.display_name, t.source_kind, t.text_key
               FROM localized_term AS t
               JOIN localized_term_name AS n USING (entity_kind, canonical_id)
               WHERE t.entity_kind = 'damage_resistance'
                 AND t.canonical_id = 'chaos'"""
        ).fetchone()
        formal_membership = self.connection.execute(
            """SELECT source_kind, primary_source_row_id, supporting_source_row_id
               FROM character_acquisition_membership
               WHERE character_id = 1004 AND acquisition_type = 'limited'"""
        ).fetchone()
        free_membership = self.connection.execute(
            """SELECT source_kind, evidence_key
               FROM character_acquisition_membership
               WHERE character_id = 1046 AND acquisition_type = 'free'"""
        ).fetchone()

        self.assertEqual(("name_missing",), normal)
        self.assertEqual(
            ("暗属性异能伤害抗性", "formal_localization", "DamageResistChaos"),
            chaos,
        )
        self.assertEqual("formal_game_data", formal_membership[0])
        self.assertTrue(formal_membership[1])
        self.assertTrue(formal_membership[2])
        self.assertEqual("reviewed_annotation", free_membership[0])
        self.assertTrue(free_membership[1])

        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "terms.sqlite3"
            with closing(sqlite3.connect(database_path)) as destination:
                self.connection.backup(destination)
                destination.execute(
                    "INSERT INTO schema_migration VALUES (30, '2026-08-30')"
                )
                destination.execute(
                    "INSERT INTO dataset VALUES ('fixture-v30', 36, '2026-08-30')"
                )
                destination.commit()
            with patch.object(static_game_data_dao, "SCHEMA_VERSION", 30):
                with StaticGameDataDao(database_path) as dao:
                    service = StaticCatalogTerminologyService(dao)
                    missing = service.resolve("damage_resistance", "normal")
                    stage = service.resolve(
                        "outer_realm_fight_stage",
                        "EAbyssFightStage::FirstHalf",
                    )
                    campaigns = service.list_fork_campaigns()
        self.assertEqual("name_missing", missing.source_kind)
        self.assertIsNone(missing.display_name)
        self.assertEqual(("上半场", "ui_state"), (stage.display_name, stage.source_kind))
        self.assertEqual(
            (
                "fork_GoldRecord", "fork_DemonBlade", "fork_Door",
                "fork_LunarPhase", "fork_GoldWool", "fork_Rose",
                "fork_Time", "fork_TigerTally",
            ),
            tuple(campaign.featured_fork_id for campaign in campaigns),
        )
        self.assertEqual(
            tuple(f"特刊{index}" for index in reversed(range(8))),
            tuple(campaign.title.display_name for campaign in campaigns),
        )

    def test_existing_release_catalogs_share_the_public_terminology_contract(self) -> None:
        release_database = Path(__file__).resolve().parents[1] / "data" / "game_static.sqlite3"
        expected = (
            ("equipment_suit", "Suit1", "「迪亚波罗斯」"),
            ("equipment_attribute", "AtkAdd", "攻击力"),
            ("monster", "Boss_07_BP", "塞润尼缇"),
            ("clone_activity_category", "clone:abyss", "轨外之境"),
            ("clone_activity", "BidKing1", "海贝场"),
            ("feast_stage", "DiyBossStage1", "无法换台之物"),
        )
        with sqlite3.connect(release_database) as connection:
            release_schema_version = int(connection.execute(
                "SELECT MAX(version) FROM schema_migration"
            ).fetchone()[0])
        with StaticGameDataDao(
            release_database,
            expected_schema_version=release_schema_version,
        ) as dao:
            service = StaticCatalogTerminologyService(dao)
            actual = tuple(
                service.resolve(entity_kind, stable_id).display_name
                for entity_kind, stable_id, _name in expected
            )

        self.assertEqual(tuple(row[2] for row in expected), actual)

    def test_only_exact_deterministic_drop_closure_becomes_complete(self) -> None:
        self.builder._import_progression_catalog()

        statuses = dict(self.connection.execute(
            "SELECT drop_id, status FROM clone_drop_projection ORDER BY drop_id"
        ))
        exact = self.connection.execute(
            """SELECT item_id, quantity FROM clone_drop_projection_item
               WHERE drop_id = 'drop_exact'"""
        ).fetchone()
        currency = self.connection.execute(
            """SELECT item_id, quantity FROM clone_drop_projection_item
               WHERE drop_id = 'drop_currency'"""
        ).fetchone()
        partial_gap = self.connection.execute(
            """SELECT reason_code FROM clone_drop_projection_gap
               WHERE drop_id = 'drop_partial'"""
        ).fetchone()

        self.assertEqual(
            {
                "drop_currency": "complete",
                "drop_exact": "complete",
                "drop_missing": "unavailable",
                "drop_partial": "unavailable",
            },
            statuses,
        )
        self.assertEqual(("MatA", 6), exact)
        self.assertEqual(("Fons", 4000), currency)
        self.assertEqual(("sequence_not_deterministic",), partial_gap)

    def test_failed_import_rolls_back_all_v30_rows_and_can_retry(self) -> None:
        self.builder.rows["fork_breakthroughs"]["Pack_1"]["NeedItems"] = "broken"
        self.connection.execute("BEGIN")
        with self.assertRaisesRegex(RuntimeError, "养成消耗格式无效"):
            self.builder._import_progression_catalog()
        self.connection.rollback()

        count = self.connection.execute(
            "SELECT COUNT(*) FROM character_release_annotation"
        ).fetchone()[0]
        self.assertEqual(0, count)

        self.builder.rows["fork_breakthroughs"]["Pack_1"]["NeedItems"] = "MatB:3"
        self.builder._import_progression_catalog()
        self.assertEqual(
            12,
            self.connection.execute(
                "SELECT COUNT(*) FROM character_release_annotation"
            ).fetchone()[0],
        )


if __name__ == "__main__":
    unittest.main()
