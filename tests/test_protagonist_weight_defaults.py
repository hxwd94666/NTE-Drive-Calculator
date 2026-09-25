# 验证主角形态共用工坊默认权重，同时保留账号与实际角色身份。
from __future__ import annotations

import json
import shutil
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from src.services.character_weight_service import (
    ensure_account_character_weights,
    reset_account_character_weights,
    save_account_character_weights,
)
from src.services.workshop_weight_template_service import (
    WorkshopWeightTemplateService,
    effective_workshop_recommended_weights,
)
from src.storage.sqlite.static_game_data_dao import StaticGameDataDao
from src.storage.sqlite.user_data_dao import UserDataDao


ROOT = Path(__file__).resolve().parents[1]


def recommendation(character_id: int, weight: float) -> dict:
    return {
        "character_id": character_id, "source_kind": "workshop_runtime",
        "source_item_id": str(character_id),
        "properties": [{"property_id": "MagBase", "weight": weight,
                        "main_weight": weight, "ordinal": 0}],
        "property_weights": {"MagBase": weight},
        "main_property_weights": {"MagBase": weight},
    }


class ProtagonistWeightDefaultTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.template = self.root / "template.json"
        self.user = self.root / "user.sqlite3"
        self.static = ROOT / "data/role_catalog/game_static.sqlite3"
        self.write_template({"1051": recommendation(1051, 1.0)})
        environment = patch.dict("os.environ", {
            "NTE_WORKSHOP_WEIGHT_TEMPLATE_FILE": str(self.template),
        })
        environment.start()
        self.addCleanup(environment.stop)
        with UserDataDao(self.user, account_id="weight-fixture"):
            pass

    def write_template(self, characters):
        self.template.write_text(json.dumps({
            "schema_version": 1, "payload_sha256": "fixture", "characters": characters,
        }), encoding="utf-8")

    def test_old_single_variant_template_resolves_both_directions(self):
        for source, target in ((1051, 1046), (1046, 1051)):
            self.write_template({str(source): recommendation(source, 1.0)})
            row = effective_workshop_recommended_weights(self.template, target, None)
            self.assertIsNotNone(row)
            self.assertEqual(row["character_id"], target)
            self.assertEqual(row["source_item_id"], str(source))
            self.assertEqual(row["property_weights"], {"MagBase": 1.0})

    def test_exact_public_id_wins_and_other_roles_do_not_borrow(self):
        self.write_template({"1046": recommendation(1046, 0.7),
                             "1051": recommendation(1051, 1.0)})
        row = effective_workshop_recommended_weights(self.template, 1046, None)
        self.assertEqual(row["property_weights"], {"MagBase": 0.7})
        fallback = {"character_id": 1003}
        self.assertIs(effective_workshop_recommended_weights(self.template, 1003, fallback), fallback)

    def test_refresh_keeps_public_variant_when_only_other_variant_requested(self):
        payload = {"code": 200, "data": [{"itemId": "1051", "name": "零",
                    "weightConfig": {"weights": [{"name": "环合强度", "value": 1.0,
                                                   "main_value": 0.8}]}}]}
        with patch("src.services.workshop_weight_template_service.urllib.request.urlopen") as opened:
            opened.return_value.__enter__.return_value.read.return_value = json.dumps(payload).encode()
            WorkshopWeightTemplateService(self.template).refresh(known_character_ids=(1046,))
        row = effective_workshop_recommended_weights(self.template, 1046, None)
        self.assertIsNotNone(row)
        self.assertEqual(row["source_item_id"], "1051")
        self.assertEqual(row["main_property_weights"], {"MagBase": 0.8})

    def test_refresh_and_reset_preserve_actual_id_and_account_edits(self):
        seeded = ensure_account_character_weights(
            self.user, (1046,), static_database_path=self.static,
        )[1046]
        self.assertEqual(seeded["property_weights"], {"MagBase": 1.0})
        saved = save_account_character_weights(
            self.user, 1046, {"MagBase": 0.0}, static_database_path=self.static,
        )
        self.assertEqual(saved["source_kind"], "account")
        self.write_template({"1051": recommendation(1051, 1.2)})
        refreshed = ensure_account_character_weights(
            self.user, (1046,), static_database_path=self.static,
        )[1046]
        self.assertEqual(refreshed["property_weights"].get("MagBase", 0.0), 0.0)
        for _ in range(2):
            reset = reset_account_character_weights(
                self.user, (1046,), static_database_path=self.static,
            )[1046]
            self.assertEqual(reset["property_weights"], {"MagBase": 1.2})
            self.assertEqual(reset["source_kind"], "default")
        with UserDataDao(self.user) as dao:
            self.assertIsNone(dao.get_character_weight_preferences(1051))

    def test_untouched_old_cache_refreshes_without_template_revision_change(self):
        with UserDataDao(self.user) as dao:
            dao.seed_character_weight_preferences(
                1046, properties=[{"property_id": "CritBase", "weight": 1.0, "main_weight": 1.0}],
                source_dataset_id="workshop-runtime:fixture", source_kind="default",
            )
        row = ensure_account_character_weights(
            self.user, (1046,), static_database_path=self.static,
        )[1046]
        self.assertEqual(row["property_weights"], {"MagBase": 1.0})

    def test_offline_reference_fallback_uses_sourced_variant(self):
        database = self.root / "reference.sqlite3"
        shutil.copy2(self.static, database)
        with closing(sqlite3.connect(database)) as connection, connection:
            connection.execute("UPDATE character_weight_recommendation SET source_kind='workshop_api', "
                               "source_item_id='1051' WHERE character_id=1051")
            connection.execute("DELETE FROM character_weight_recommendation_property WHERE character_id=1051")
            connection.execute("INSERT INTO character_weight_recommendation_property "
                               "(character_id,property_id,weight,main_weight,ordinal) VALUES (1051,'MagBase',1,1,0)")
        self.template.unlink()
        with StaticGameDataDao(database) as dao:
            row = dao.get_character_recommended_weights(1046)
        self.assertEqual(row["character_id"], 1046)
        self.assertEqual(row["source_item_id"], "1051")
        self.assertEqual(row["property_weights"], {"MagBase": 1.0})
        reset = reset_account_character_weights(self.user, (1046,), static_database_path=database)[1046]
        self.assertEqual(reset["property_weights"], {"MagBase": 1.0})


if __name__ == "__main__":
    unittest.main()
