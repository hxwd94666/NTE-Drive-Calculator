# 验证战报面板重建只复用单次请求的静态资源，保持角色与冻结属性独立。
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from src.services.battle_report.build.stat_reconstruction import BattleBuildStatReconstructionService


class BattleBuildStatResourceReuseTests(unittest.TestCase):
    def test_frozen_request_reuses_role_resources_without_sharing_candidate_profiles(self):
        dependencies = SimpleNamespace(
            static_database_path=Path("fixture-static.sqlite3"),
            user_database_path=Path("fixture-user.sqlite3"),
        )
        details = {}
        original = {"character_id": 1, "profile": {"level": 80}, "world_bonus": {"sentinel": 1}}
        seen_profiles = []

        def sources(detail, _items):
            seen_profiles.append(detail["profile"]["level"])
            detail["world_bonus"]["changed"] = True
            return {}

        def build(level):
            return {"characters": [{"character_id": 1, "profile": {"level": level}, "stats": []}]}

        with (patch("src.services.battle_report.build.stat_reconstruction.load_official_role_detail",
                    return_value=original) as load,
              patch("src.services.battle_report.build.stat_reconstruction.calculate_official_role_combat_stat_sources",
                    side_effect=sources),
              patch("src.services.battle_report.build.stat_reconstruction.calculate_official_role_combat_stat_components",
                    return_value=())):
            BattleBuildStatReconstructionService.enrich(build(20), dependencies, detail_cache=details)
            BattleBuildStatReconstructionService.enrich(build(40), dependencies, detail_cache=details)
            self.assertEqual(1, load.call_count)
        self.assertEqual([20, 40], seen_profiles)
        self.assertEqual(80, details[1]["profile"]["level"])
        self.assertNotIn("changed", details[1]["world_bonus"])

    def test_each_enrich_reuses_resources_but_rebuilds_each_mutable_detail(self):
        caches, details = [], []
        dependencies = SimpleNamespace(
            static_database_path=Path("fixture-static.sqlite3"),
            user_database_path=Path("fixture-user.sqlite3"),
        )

        def load(_user_database, character_id, **kwargs):
            self.assertIs(kwargs["static_database_path"], dependencies.static_database_path)
            self.assertFalse(kwargs["include_inventory_contexts"])
            cache = kwargs["request_cache"]
            cache.setdefault("static-resource", object())
            caches.append(cache)
            detail = {"character_id": character_id, "world_bonus": {"sentinel": character_id}}
            details.append(detail)
            return detail

        def build():
            return {"characters": [{
                "character_id": index, "profile": {"level": 10 * index},
                "stats": [{"source_group": "world_bonus", "property_id": "AtkAdd", "value": float(index)}],
            } for index in (1, 2)]}

        with (patch("src.services.battle_report.build.stat_reconstruction.load_official_role_detail", side_effect=load),
              patch("src.services.battle_report.build.stat_reconstruction.calculate_official_role_combat_stat_sources", return_value={}),
              patch("src.services.battle_report.build.stat_reconstruction.calculate_official_role_combat_stat_components", return_value=())):
            first, second = build(), build()
            BattleBuildStatReconstructionService.enrich(first, dependencies)
            BattleBuildStatReconstructionService.enrich(second, dependencies)
        self.assertIs(caches[0], caches[1])
        self.assertIs(caches[2], caches[3])
        self.assertIsNot(caches[0], caches[2])
        self.assertIsNot(caches[0]["static-resource"], caches[2]["static-resource"])
        self.assertEqual(len({id(detail) for detail in details}), 4)
        self.assertEqual([detail["profile"]["level"] for detail in details], [10, 20, 10, 20])
        self.assertEqual([detail["world_bonus"]["yaodao_attack_add"] for detail in details], [1., 2., 1., 2.])

    def test_missing_static_database_does_not_load_or_mutate_build(self):
        build = {"characters": [{"character_id": 1}]}
        with patch("src.services.battle_report.build.stat_reconstruction.load_official_role_detail") as load:
            BattleBuildStatReconstructionService.enrich(build, SimpleNamespace(static_database_path=None))
        load.assert_not_called()
        self.assertEqual(build, {"characters": [{"character_id": 1}]})
