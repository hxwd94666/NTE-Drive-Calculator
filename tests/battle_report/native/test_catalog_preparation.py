# 对照公开静态库验证 Rust 面板与来源重建，使用显式独立探针程序。
from copy import deepcopy
from dataclasses import asdict, replace
import json
import os
from pathlib import Path
import subprocess
import unittest

from src.domain.official_role.advancement import fork_active_panel_stats
from src.domain.battle_report.buffs.models import BattleInferredBuffInterval
from src.domain.battle_report.replay.models import BattleSkillDamageEvidence
from src.domain.battle_report.timeline.events import BattleTreatmentEvent
from src.integrations.battle_analysis.page_wire import decode
from tests.test_native_catalog_states import normalize
from src.services.battle_report.build.stat_reconstruction import BattleBuildStatReconstructionService
from src.services.battle_report.buffs.inference import BattleBuffInferenceService
from src.services.battle_report.build.offline_analysis import _baselines
from src.services.battle_report.persistence import BattleReportPersistenceDependencies
from src.storage.sqlite.static_game_data_dao import StaticGameDataDao
from src.services.battle_report.damage.skill_evidence import BattleSkillDamageEvidenceService
from tests.battle_report.mechanics.test_dot_state import _hit
from types import SimpleNamespace
import sqlite3


@unittest.skipUnless(os.environ.get("NTE_CATALOG_PROBE"), "explicit catalog probe not configured")
class NativeCatalogPreparationTests(unittest.TestCase):
    def test_full_state_orchestration_uses_same_order_and_half_boundaries(self):
        from src.services.battle_report.characters.zankou import BattleZankouFormBuffService
        from src.services.battle_report.characters.shinku import BattleShinkuRageBuffService
        from src.services.battle_report.characters.fadia import BattleFadiaHpStackService
        from src.services.battle_report.characters.daffodill import BattleDaffodillAwakeningService
        from src.services.battle_report.treatment.replay import BattleTreatmentReplayService
        from src.services.battle_report.environment.half_buff_scope import BattleHalfBuffScopeService
        from tests.battle_report.characters.test_zankou_form import _action
        static_path = Path("data/game_static.sqlite3").resolve()
        build = {"characters": [{"character_id": cid, "observed_name": str(cid), "breakthrough_stage": 6,
                  "awakening_level": 6, "profile": {}, "stats": [{"source_group": "resolved",
                  "property_id": "PanelHP", "value": 20_000.0}]} for cid in (1036, 1039, 1054, 1075, 1076)]}
        hits = (_hit("fantasy", 1_000_000, "GE_Player_Zankou_Skill1_Damage", 1036),
                _hit("rage", 2_000_000, "GE_Player_Shinku_Skill1_Rage_Damage", 1076),
                _hit("dark-star", 3_000_000, "Buff_Reaction_4_new", 1039),
                _hit("topple", 5_000_000, "Buff_Tenacity_Damage", 1054))
        actions = (_action("fantasy", 500_000, 1_500_000, "GE_Player_Zankou_Skill1_Damage"),
                   _action("healing", 2_000_000, 2_500_000, character_id=1075, input_kind="E"))
        end, stops = 12_000_000, ((4_000_000, 4_500_000),)
        raw = [{"abyss_half": "upper", "relative_time_us": 0, "character_id": 1036},
               {"abyss_half": "lower", "relative_time_us": 7_000_000, "character_id": 1039}]
        with StaticGameDataDao(static_path) as dao:
            zconfig = BattleZankouFormBuffService.load_config(dao)
            sconfig = BattleShinkuRageBuffService.load_config(dao)
            rules = BattleBuffInferenceService.load_rules(dao, build)
        for infer_buffs in (False, True):
            forms = BattleZankouFormBuffService.infer(build=build, actions=actions, hits=hits,
                    battle_end_us=end, config=zconfig, time_stop_intervals=stops) if infer_buffs else ()
            treatment = BattleTreatmentReplayService.infer(build=build, actions=actions, hits=hits,
                    battle_end_us=end, time_stop_intervals=stops, state_buff_intervals=forms,
                    zankou_effect_three_recover_ratio=zconfig.effect_three_recover_ratio, infer_buffs=infer_buffs)
            intervals = ()
            if infer_buffs:
                intervals = (*BattleBuffInferenceService.infer(rules, actions=actions, hits=hits,
                    battle_end_us=end, time_stop_intervals=stops, treatment_events=treatment.events),
                    *treatment.buff_intervals, *BattleFadiaHpStackService.infer(build=build, hits=hits, battle_end_us=end),
                    *forms, *BattleShinkuRageBuffService.infer(build=build, hits=hits, config=sconfig),
                    *BattleDaffodillAwakeningService.infer(build=build, actions=actions, hits=hits,
                        battle_end_us=end, time_stop_intervals=stops))
                intervals = BattleHalfBuffScopeService.scope(intervals, raw_hits=raw, battle_end_us=end)
            request = {"operation": "full_state", "analysis": {"hits": hits, "inferred_actions": actions,
                        "battle_end_us": end, "time_stop_intervals": stops, "raw_hits": raw},
                        "build": build, "rules": rules, "config": {"zankou": zconfig, "shinku": sconfig, "outer": None},
                        "condition": None, "critical": [], "infer_buffs": infer_buffs}
            completed = subprocess.run([os.environ["NTE_CATALOG_PROBE"]], input=json.dumps(normalize(request)),
                                       text=True, encoding="utf-8", capture_output=True, check=True)
            actual = json.loads(completed.stdout)
            # 只投影两类已声明的展示 DTO；外层集合、顺序和全部声明字段仍交原比较器核对。
            actual["buff_intervals"] = normalize(decode(actual["buff_intervals"], tuple[BattleInferredBuffInterval, ...]))
            actual["treatment_events"] = normalize(decode(actual["treatment_events"], tuple[BattleTreatmentEvent, ...]))
            self.assert_json_equal(normalize({"buff_intervals": intervals, "treatment_events": treatment.events}), actual)

    def test_static_character_configs_and_each_season_recovery(self):
        from src.services.battle_report.characters.zankou import BattleZankouFormBuffService
        from src.services.battle_report.characters.shinku import BattleShinkuRageBuffService
        from src.services.battle_report.environment.outer_realm_buffs import BattleOuterRealmBuffService
        static_path = Path("data/game_static.sqlite3").resolve()
        with StaticGameDataDao(static_path) as dao:
            base = {"zankou": asdict(BattleZankouFormBuffService.load_config(dao)),
                    "shinku": asdict(BattleShinkuRageBuffService.load_config(dao))}
            environments = ["", "unresolved", *[f"{r['level_config_id']}|{r['level_id']}|{r['fight_stage']}"
                            for r in dao._rows("SELECT DISTINCT level_config_id,level_id,fight_stage FROM abyss_level_monster_spawn")]]
        for environment in environments:
            config = BattleOuterRealmBuffService.load(static_path, environment)
            expected = {**base, "outer": asdict(config) if config else None}
            request = {"operation": "configs", "static_path": str(static_path), "environment_ref": environment}
            completed = subprocess.run([os.environ["NTE_CATALOG_PROBE"]], input=json.dumps(request),
                                       text=True, encoding="utf-8", capture_output=True, check=True)
            self.assert_json_equal(json.loads(json.dumps(expected)), json.loads(completed.stdout))

    def test_all_static_skill_rows_and_awakened_frozen_owners(self):
        static_path = Path("data/game_static.sqlite3").resolve()
        with sqlite3.connect(static_path) as conn:
            damage_ids = [r[0] for r in conn.execute("SELECT damage_id FROM skill_damage ORDER BY damage_id")]
        with StaticGameDataDao(static_path) as dao:
            build = {"characters": [{"character_id": row["character_id"], "character_level": 80,
                     "breakthrough_stage": 6, "awakening_level": 6,
                     "profile": {"awakening_selection_initialized": True,
                                 "selected_awaken_effect_ids": [f"Effect{i}" for i in range(1, 7)]}}
                    for row in dao.list_characters()]}
            # 本例核静态 GE 与冻结 owner；通用 dot 夹具的默认普攻 ability 并非这些 GE 的观测证据。
            hits = tuple(replace(_hit(f"static-{i}", i * 2_000_000, damage_id,
                         next(iter(dao.list_skill_damage_owner_character_ids(damage_id)), 1003)), ability_id="")
                         for i, damage_id in enumerate(damage_ids))
            analysis = SimpleNamespace(hits=hits, timeline_hits=hits, inferred_actions=(),
                                       time_stop_intervals=(), axis_complete=True, linko_coattack_inferences=())
            expected = [asdict(row) for row in BattleSkillDamageEvidenceService.load(dao, analysis, build)]
        request = {"operation": "skill", "static_path": str(static_path), "build": build,
                   "analysis": {**vars(analysis), "hits": [asdict(h) for h in hits],
                                "timeline_hits": [asdict(h) for h in hits]}}
        completed = subprocess.run([os.environ["NTE_CATALOG_PROBE"]], input=json.dumps(request, ensure_ascii=False),
                                   text=True, encoding="utf-8", capture_output=True, check=True)
        actual = decode(json.loads(completed.stdout)["evidence"], tuple[BattleSkillDamageEvidence, ...])
        self.assert_json_equal(json.loads(json.dumps(expected)), normalize(actual))

    def test_skill_query_cache_keeps_observed_ability_and_each_frozen_build(self):
        static_path = Path("data/game_static.sqlite3").resolve()
        with sqlite3.connect(static_path) as conn:
            damages = conn.execute("SELECT damage_id, ability_id FROM skill_damage "
                                   "WHERE ability_id IS NOT NULL AND ability_id <> '' "
                                   "ORDER BY damage_id LIMIT 30").fetchall()
        with StaticGameDataDao(static_path) as dao:
            characters = [{"character_id": row["character_id"], "character_level": 80,
                           "breakthrough_stage": 6, "awakening_level": 6,
                           "profile": {"awakening_selection_initialized": True,
                                       "selected_awaken_effect_ids": ["Effect3"]}}
                          for row in dao.list_characters()]
            hits = []
            for damage_id, ability in damages:
                owner = next(iter(dao.list_skill_damage_owner_character_ids(damage_id)), 1003)
                for observed in (ability, "", "unknown_fixture_ability", ability):
                    hits.append(replace(_hit(f"cache-{len(hits)}", len(hits) * 2_000_000,
                                             damage_id, owner), ability_id=observed))
            analysis = SimpleNamespace(hits=tuple(hits), timeline_hits=tuple(hits), inferred_actions=(),
                                       time_stop_intervals=(), axis_complete=True, linko_coattack_inferences=())
            for level in (1, 10):
                build = {"characters": deepcopy(characters)}
                for character in build["characters"]:
                    character["profile"]["skill_levels"] = {ability: level for _, ability in damages}
                expected = [asdict(row) for row in BattleSkillDamageEvidenceService.load(dao, analysis, build)]
                request = {"operation": "skill", "static_path": str(static_path), "build": build,
                           "analysis": {**vars(analysis), "hits": [asdict(h) for h in hits],
                                        "timeline_hits": [asdict(h) for h in hits]}}
                completed = subprocess.run([os.environ["NTE_CATALOG_PROBE"]],
                    input=json.dumps(request), text=True, encoding="utf8", capture_output=True, check=True)
                actual = decode(json.loads(completed.stdout)["evidence"], tuple[BattleSkillDamageEvidence, ...])
                self.assert_json_equal(json.loads(json.dumps(expected)), normalize(actual))

    def test_all_static_forks_suits_and_character_rule_declarations(self):
        static_path = Path("data/game_static.sqlite3").resolve()
        builds = []
        with StaticGameDataDao(static_path) as dao:
            def character(identity=1072):
                return {"character_id": identity, "observed_name": "公开样例角色", "breakthrough_stage": 6,
                        "stats": [{"source_group": "character", "property_id": "AtkBase", "value": 1234.5}],
                        "profile": {"awakening_selection_initialized": True,
                                    "selected_awaken_effect_ids": [f"Effect{i}" for i in range(1, 7)],
                                    "skill_levels": {"GA_Haniel_Skill": 3, "GA_Haniel_UltraSkill": 10}}}
            for fork in dao.list_forks():
                for level in (1, 3, 5):
                    builds.append({"characters": [{**character(), "fork_id": fork["fork_id"], "fork_refinement_level": level}]})
            for suit in dao.list_suits():
                equipment = [{"kind": "core", "suit_id": suit["suit_id"]}]
                equipment.extend({"kind": "module", "geometry": shape} for shape in suit["required_shape_ids"])
                builds.append({"characters": [{**character(), "equipment": equipment}]})
            for raw in dao.list_characters():
                builds.append({"characters": [character(raw["character_id"])]})
            expected = [[asdict(rule) for rule in BattleBuffInferenceService.load_rules(dao, build)] for build in builds]
        request = {"operation": "rules_batch", "static_path": str(static_path), "builds": builds}
        completed = subprocess.run([os.environ["NTE_CATALOG_PROBE"]], input=json.dumps(request, ensure_ascii=False),
                                   text=True, encoding="utf-8", capture_output=True, check=True)
        self.assert_json_equal(json.loads(json.dumps(expected)), json.loads(completed.stdout)["rules"])

    def test_rule_cache_tracks_source_attack_levels_awakenings_and_preserves_stat_independence(self):
        static_path = Path("data/game_static.sqlite3").resolve()
        base = {"character_id": 1020, "observed_name": "公开样例", "breakthrough_stage": 6,
                "profile": {"awakening_selection_initialized": True, "selected_awaken_effect_ids": [],
                            "skill_levels": {"GA_Haniel_Skill": 3, "GA_Haniel_UltraSkill": 5}},
                "stats": [{"source_group": "character", "property_id": "AtkBase", "value": 1234.0}]}
        characters = [deepcopy(base) for _ in range(8)]
        characters[1]["stats"][0]["value"] = 2468.0
        characters[2]["profile"]["skill_levels"]["GA_Haniel_Skill"] = 8
        characters[3]["profile"]["selected_awaken_effect_ids"] = ["Effect2", "Effect3"]
        characters[4]["skills"] = [{"skill_id": "GA_Haniel_UltraSkill", "skill_level": 10}]
        characters[5]["stats"].append({"source_group": "equipment", "property_id": "AtkBase", "value": 900.0})
        characters[6]["observed_name"] = "另一公开样例"
        # Repeat the unchanged input after all intervening keys to exercise retrieval.
        builds = [{"characters": [character]} for character in characters]
        with StaticGameDataDao(static_path) as dao:
            expected = [[asdict(rule) for rule in BattleBuffInferenceService.load_rules(dao, build)] for build in builds]
        self.assertEqual(expected[0], expected[5])
        self.assertEqual(expected[0], expected[7])
        for index in (1, 2, 3, 4, 6):
            self.assertNotEqual(expected[0], expected[index])
        request = {"operation": "rules_batch", "static_path": str(static_path), "builds": builds}
        completed = subprocess.run([os.environ["NTE_CATALOG_PROBE"]], input=json.dumps(request, ensure_ascii=False),
                                   text=True, encoding="utf-8", capture_output=True, check=True)
        self.assert_json_equal(json.loads(json.dumps(expected)), json.loads(completed.stdout)["rules"])

    def test_missing_build_and_unknown_character_keep_unknown_frozen_facts(self):
        from unittest.mock import patch
        static_path = Path("data/game_static.sqlite3").resolve()
        unknown = {"characters": [{"character_id": 999999, "observed_name": "  ",
                   "profile": {}, "stats": [], "equipment": []}]}
        with StaticGameDataDao(static_path) as dao:
            self.assertIsNone(dao.get_character(999999))
        expected = deepcopy(unknown)
        with patch("src.services.battle_report.build.stat_reconstruction.load_official_role_detail",
                   side_effect=ValueError("unknown public fixture")):
            BattleBuildStatReconstructionService.enrich(expected,
                BattleReportPersistenceDependencies("fixture", Path("unused-fixture.sqlite3"), 0, static_path))
        for build in (None, {}, {"characters": None}, unknown):
            input_before = deepcopy(build)
            request = {"static_path": str(static_path), "build": build, "world_bonus": {}}
            completed = subprocess.run([os.environ["NTE_CATALOG_PROBE"]], input=json.dumps(request),
                                       text=True, encoding="utf-8", capture_output=True, check=True)
            actual = json.loads(completed.stdout)
            wanted = expected if build is unknown else build
            projected = deepcopy(actual)
            if build is unknown:
                character = projected["build"]["characters"][0]
                # 空 profile 的未知角色没有正式名或已包含弧盘常驻证据；显式 null 不能被默认事实替代。
                self.assertIsNone(character["character_name"])
                self.assertIsNone(character["included_fork_permanent"])
                self.assertEqual(character["observed_name"], unknown["characters"][0]["observed_name"])
                character.pop("character_name")
                character.pop("included_fork_permanent")
            self.assertEqual(projected["build"], wanted)
            self.assertEqual(build, input_before)
            self.assert_json_equal(json.loads(json.dumps([asdict(row) for row in _baselines(wanted)])), actual["baselines"])

    def test_all_static_characters_frozen_panels_and_sources(self):
        static_path = Path("data/game_static.sqlite3").resolve()
        world = {"yaodao_attack_add": 13.0, "quantao_crit_damage": 0.025}
        details, characters, metadata = {}, [], {}
        with StaticGameDataDao(static_path) as dao:
            forks = dao.list_fork_templates()
            attributes = {row["attribute_id"]: row for row in dao.list_equipment_attributes()}
            for index, raw in enumerate(dao.list_characters()):
                identity = raw["character_id"]
                growth = dao.list_character_panel_growth(identity)
                if not growth:
                    continue
                best = max(growth, key=lambda row: (row["level"], row["breakthrough_stage"]))
                fork = forks[index % len(forks)]
                level = max((row["level"] for row in fork["upgrade_levels"]), default=1)
                active = fork_active_panel_stats(fork, level, refinement_level=3)
                metadata[identity] = {
                    "character_name": raw["name_zh"],
                    "fork_values": {key: value for key, value in active.items() if value != 0},
                    "permanent_rows": dao._rows(
                        "SELECT * FROM fork_permanent_property WHERE fork_id=? AND refinement_level=?",
                        (fork["fork_id"], 3),
                    ),
                }
                details[identity] = {
                    "growth_rows": growth, "forks": forks, "attributes": attributes,
                    "likeability_bonus": dao.get_character_likeability_bonus(identity),
                    "shape_bonus": dao.get_character_shape_bonus(identity), "world_bonus": world,
                }
                characters.append({
                    "character_id": identity, "observed_name": raw["name_zh"],
                    "character_level": best["level"], "breakthrough_stage": best["breakthrough_stage"],
                    "awakening_level": 3, "stats": [], "equipment": [
                        {"kind": "module", "grid_count": 5, "quality": "Gold", "stats": [
                            {"stat_group": "sub", "property_id": "CritBase", "value": 0.04, "is_percent": True},
                        ]},
                        {"kind": "core", "stats": [{"stat_group": "main", "property_id": "AtkUp", "value": 0.20}]},
                    ],
                    "profile": {
                        "character_level": best["level"], "breakthrough_stage": best["breakthrough_stage"],
                        "fork_id": fork["fork_id"], "fork_level": level,
                        "fork_refinement_level": 3, "likeability_level_10_enabled": True,
                        "awakening_selection_initialized": True, "selected_awaken_effect_ids": ["Effect1", "Effect3", "Effect5"],
                    },
                })
        build = {"characters": characters}
        input_before = deepcopy(build)
        expected = deepcopy(build)
        BattleBuildStatReconstructionService.enrich(
            expected, BattleReportPersistenceDependencies("fixture", Path("unused-fixture.sqlite3"), 0, static_path),
            detail_cache=details,
        )
        request = {"static_path": str(static_path), "build": build, "world_bonus": world}
        completed = subprocess.run([os.environ["NTE_CATALOG_PROBE"]], input=json.dumps(request, ensure_ascii=False),
                                   text=True, encoding="utf-8", capture_output=True, check=True)
        actual = json.loads(completed.stdout)
        projected = deepcopy(actual)
        self.assertEqual(len(projected["build"]["characters"]), len(characters))
        for index, (character, original) in enumerate(zip(projected["build"]["characters"], characters, strict=True)):
            evidence = metadata[original["character_id"]]
            self.assertEqual(character["character_name"], evidence["character_name"])
            self.assertEqual(character["observed_name"], original["observed_name"])
            fork_rows = [row for row in character["stats"] if row["source_group"] == "fork" and row["value"] != 0]
            self.assertEqual(len({row["property_id"] for row in fork_rows}), len(fork_rows))
            values = {row["property_id"]: row["value"] for row in fork_rows}
            marker = character["included_fork_permanent"]
            # 只有整组非零来源与正式完整弧盘面板相等才认定 marker；多行常驻绝不取 first 作期望。
            if evidence["permanent_rows"] and values == evidence["fork_values"]:
                self.assertIn(marker, evidence["permanent_rows"])
            else:
                self.assertIsNone(marker, f"characters[{index}] complete fork={evidence['fork_values']}; actual fork={values}")
            character.pop("character_name")
            character.pop("included_fork_permanent")
        self.assertEqual(build, input_before)
        expected_result = {"build": expected, "baselines": [asdict(row) for row in _baselines(expected)]}
        self.assert_json_equal(json.loads(json.dumps(expected_result)), projected)

    def assert_json_equal(self, expected, actual, path="result"):
        if isinstance(expected, dict):
            self.assertEqual(set(expected), set(actual), path)
            for key in expected:
                self.assert_json_equal(expected[key], actual[key], f"{path}.{key}")
        elif isinstance(expected, list):
            self.assertEqual(len(expected), len(actual), path)
            for index, (left, right) in enumerate(zip(expected, actual, strict=True)):
                self.assert_json_equal(left, right, f"{path}[{index}]")
        elif isinstance(expected, float):
            self.assertAlmostEqual(expected, actual, places=9, msg=path)
        else:
            self.assertEqual(expected, actual, path)
