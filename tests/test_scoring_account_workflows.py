# 覆盖用户工作流相关的回归测试。
import json
import os
import tempfile
import unittest
import urllib.error
import zipfile
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

class ScoringScreeningWorkflowTests(unittest.TestCase):
    def _write_scoring_config(self, config_dir: Path):
        roles = {
            "A": {
                "default_set": "Set",
                "extra_shape_label": "X",
                "board_matrix": [[1]],
                "weights": {
                    "Wanted": 1.0,
                    "Other": 2.0,
                    "Sub": 1.0,
                    "H1": 10.0,
                    "H2": 10.0,
                    "Crit": 16.0,
                },
            }
        }
        stats = {
            "gold_base_values": {"Sub": 1.0, "H1": 1.0, "H2": 1.0, "Crit": 1.0},
            "tape_main_stats_pool": ["Wanted", "Other"],
            "tape_main_stat_values": {"Wanted": 1.0, "Other": 1.0},
            "tape_stat_values": {},
            "main_only_keywords": [],
            "stat_alias_mapping": {},
            "benefit_one": {},
            "benefit_alias_mapping": {},
            "weight_pool": [],
        }
        (config_dir / "roles.json").write_text(json.dumps(roles, ensure_ascii=False), encoding="utf-8")
        (config_dir / "stats.json").write_text(json.dumps(stats, ensure_ascii=False), encoding="utf-8")

    def test_workshop_weight_merge_updates_weights_and_preserves_local_role_fields(self):
        from src.domain.stat_catalog import StatCatalog
        from src.features.settings.workshop_weights import (
            merge_workshop_weights_into_roles,
            parse_workshop_role_weights,
        )

        catalog = StatCatalog.from_config_dir("config")
        records = [
            {
                "itemId": "chr_a",
                "name": "A",
                "weightConfig": {
                    "weights": [
                        {"name": "大攻击", "key": "attack", "value": 0.8, "main_value": 1.2},
                        {"name": "暴击率", "key": "crit", "value": 1.0},
                        {"name": "通用伤害增强", "key": "damage", "value": 0.7},
                        {"name": "无效", "key": "none", "value": 0},
                    ]
                },
            },
            {
                "itemId": "chr_missing",
                "name": "Missing",
                "weightConfig": [{"name": "暴击伤害", "key": "crit_dmg", "value": 0.9}],
            },
        ]
        roles = {
            "A": {
                "default_set": "Set",
                "board_matrix": [[0]],
                "weights": {"old": 1.0},
            },
            "B": {"default_set": "Set", "weights": {"keep": 1.0}},
        }

        workshop_roles = parse_workshop_role_weights(records, catalog)
        merged, summary = merge_workshop_weights_into_roles(roles, workshop_roles)

        self.assertEqual(1, summary["updated_count"])
        self.assertEqual(1, summary["skipped_count"])
        self.assertEqual({"攻击力%": 0.8, "暴击率%": 1.0, "伤害增加%": 0.7}, merged["A"]["weights"])
        self.assertEqual({"攻击力%": 1.2}, merged["A"]["main_weights"])
        self.assertEqual("chr_a", merged["A"]["workshop_item_id"])
        self.assertEqual("Set", merged["A"]["default_set"])
        self.assertEqual([[0]], merged["A"]["board_matrix"])
        self.assertEqual({"keep": 1.0}, merged["B"]["weights"])

    def test_scoring_accepts_legacy_workshop_damage_weight_name(self):
        from src.optimizer.scoring import ScoringEngine

        engine = ScoringEngine("config")
        self.assertEqual(0.7, engine._get_flexible_weight("伤害增加%", {"通用伤害增强": 0.7}))
        self.assertEqual(0.7, engine._get_flexible_weight("通用伤害增强", {"伤害增加%": 0.7}))

    def test_workshop_weight_merge_maps_traveler_gender_names_to_local_main_character(self):
        from src.domain.stat_catalog import StatCatalog
        from src.features.settings.workshop_weights import (
            merge_workshop_weights_into_roles,
            parse_workshop_role_weights,
        )

        catalog = StatCatalog.from_config_dir("config")
        records = [
            {
                "itemId": "1046",
                "name": "异能者(男)",
                "weightConfig": {"weights": [{"name": "环合强度", "key": "arcane", "value": 1.0}]},
            },
            {
                "itemId": "1051",
                "name": "异能者(女)",
                "weightConfig": {"weights": [{"name": "环合强度", "key": "arcane", "value": 1.0}]},
            },
        ]
        roles = {
            "主角": {
                "default_set": "Set",
                "weights": {"old": 0.1},
            }
        }

        workshop_roles = parse_workshop_role_weights(records, catalog)
        merged, summary = merge_workshop_weights_into_roles(roles, workshop_roles)

        self.assertEqual(1, summary["updated_count"])
        self.assertEqual(0, summary["skipped_count"])
        self.assertEqual({"环合强度": 1.0}, merged["主角"]["weights"])
        self.assertEqual("1046", merged["主角"]["workshop_item_id"])
        self.assertEqual(["1046", "1051"], merged["主角"]["workshop_item_ids"])
        self.assertEqual(["异能者(男)", "异能者(女)"], merged["主角"]["workshop_aliases"])

    def test_scoring_uses_main_weights_for_tape_main_stat_when_present(self):
        from src.models.equipment import Tape
        from src.optimizer.scoring import ScoringEngine

        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp)
            (config_dir / "stats.json").write_text(
                json.dumps(
                    {
                        "gold_base_values": {"Sub": 1.0, "Main": 1.0},
                        "tape_main_stat_values": {"Main": 1.0},
                        "main_only_keywords": [],
                        "stat_alias_mapping": {},
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (config_dir / "roles.json").write_text(
                json.dumps(
                    {
                        "A": {
                            "weights": {"Sub": 1.0, "Main": 0.1},
                            "main_weights": {"Main": 2.0},
                        }
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            tape = Tape(
                uid="tape",
                quality="Gold",
                area=15,
                set_name="Set",
                main_stats="Main",
                sub_stats={},
            )

            engine = ScoringEngine(str(config_dir))
            engine.evaluate_global_inventory([tape])

        self.assertEqual(100.0, tape.role_scores["A"])

    def test_tape_main_filter_is_applied_before_tape_top_limit(self):
        from src.models.equipment import Tape
        from src.optimizer.scoring import ScoringEngine

        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp)
            self._write_scoring_config(config_dir)
            tapes = [
                Tape(uid=f"other_{idx}", quality="Gold", area=15, set_name="Set", main_stats="Other", sub_stats={})
                for idx in range(4)
            ]
            tapes.append(
                Tape(uid="wanted", quality="Gold", area=15, set_name="Set", main_stats="Wanted", sub_stats={"Sub": 1})
            )

            result = ScoringEngine(str(config_dir)).evaluate_global_inventory(
                tapes,
                tape_top_k_per_set_per_role=3,
                tape_main_filters={"A": ["Wanted"]},
            )

            self.assertEqual(["wanted"], [tape.uid for tape in result["tapes"]["A"]])

    def test_tape_main_filter_allows_zero_score_matching_tape(self):
        from src.models.equipment import Tape
        from src.optimizer.scoring import ScoringEngine

        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp)
            self._write_scoring_config(config_dir)
            roles_path = config_dir / "roles.json"
            roles = json.loads(roles_path.read_text(encoding="utf-8"))
            roles["A"]["weights"] = {"Sub": 1.0}
            roles_path.write_text(json.dumps(roles, ensure_ascii=False), encoding="utf-8")

            result = ScoringEngine(str(config_dir)).evaluate_global_inventory(
                [
                    Tape(uid="wanted_zero", quality="Gold", area=15, set_name="Set", main_stats="Wanted", sub_stats={}),
                    Tape(uid="other_high", quality="Gold", area=15, set_name="Set", main_stats="Other", sub_stats={"Sub": 1}),
                ],
                tape_top_k_per_set_per_role=3,
                tape_main_filters={"A": ["Wanted"]},
            )

            self.assertEqual(["wanted_zero"], [tape.uid for tape in result["tapes"]["A"]])

    def test_stat_priority_is_applied_before_drive_top_limit(self):
        from src.models.equipment import Drive
        from src.optimizer.scoring import ScoringEngine

        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp)
            self._write_scoring_config(config_dir)
            drives = [
                Drive(
                    uid=f"high_{idx}",
                    quality="Gold",
                    area=1,
                    shape_id="S1",
                    set_name="Set",
                    main_stats={"m1": 1, "m2": 1},
                    sub_stats={"H1": 1, "H2": 1},
                )
                for idx in range(3)
            ]
            drives.append(
                Drive(
                    uid="crit",
                    quality="Gold",
                    area=1,
                    shape_id="S1",
                    set_name="Set",
                    main_stats={"m1": 1, "m2": 1},
                    sub_stats={"Crit": 1},
                )
            )

            result = ScoringEngine(str(config_dir)).evaluate_global_inventory(
                drives,
                top_k_per_shape_per_role=3,
                crit_priority_modes={"A": {"stats": ["Crit"], "equal_priority": False}},
            )

            self.assertIn("crit", [drive.uid for drive in result["drives"]])

    def test_stat_priority_can_ignore_grade_limit_before_drive_top_limit(self):
        from src.models.equipment import Drive
        from src.optimizer.scoring import ScoringEngine

        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp)
            self._write_scoring_config(config_dir)
            roles_path = config_dir / "roles.json"
            roles = json.loads(roles_path.read_text(encoding="utf-8"))
            roles["A"]["weights"] = {"H1": 10.0, "Crit": 1.0}
            roles_path.write_text(json.dumps(roles, ensure_ascii=False), encoding="utf-8")

            high_score = Drive(
                uid="high_score",
                quality="Gold",
                area=1,
                shape_id="S1",
                set_name="Set",
                main_stats={"m1": 1, "m2": 1},
                sub_stats={"H1": 1},
            )
            low_score_match = Drive(
                uid="low_score_match",
                quality="Gold",
                area=1,
                shape_id="S1",
                set_name="Set",
                main_stats={"m1": 1, "m2": 1},
                sub_stats={"Crit": 1},
            )

            result = ScoringEngine(str(config_dir)).evaluate_global_inventory(
                [high_score, low_score_match],
                top_k_per_shape_per_role=1,
                crit_priority_modes={"A": {"stats": ["Crit"], "ignore_grade_limit": True}},
            )

            self.assertEqual(["high_score", "low_score_match"], [drive.uid for drive in result["drives"]])

    def test_stat_priority_ignore_grade_limit_keeps_zero_score_matching_drive(self):
        from src.models.equipment import Drive
        from src.optimizer.scoring import ScoringEngine

        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp)
            self._write_scoring_config(config_dir)
            roles_path = config_dir / "roles.json"
            roles = json.loads(roles_path.read_text(encoding="utf-8"))
            roles["A"]["weights"] = {"H1": 10.0}
            roles_path.write_text(json.dumps(roles, ensure_ascii=False), encoding="utf-8")

            high_score = Drive(
                uid="high_score",
                quality="Gold",
                area=1,
                shape_id="S1",
                set_name="Set",
                main_stats={"m1": 1, "m2": 1},
                sub_stats={"H1": 1},
            )
            zero_score_match = Drive(
                uid="zero_score_match",
                quality="Gold",
                area=1,
                shape_id="S1",
                set_name="Set",
                main_stats={"m1": 1, "m2": 1},
                sub_stats={"Crit": 1},
            )

            result = ScoringEngine(str(config_dir)).evaluate_global_inventory(
                [high_score, zero_score_match],
                top_k_per_shape_per_role=1,
                crit_priority_modes={"A": {"stats": ["Crit"], "ignore_grade_limit": True}},
            )

            self.assertEqual(["high_score", "zero_score_match"], [drive.uid for drive in result["drives"]])

    def test_strategy_rank_respects_ignore_grade_limit_for_low_score_drive(self):
        from src.models.equipment import Drive
        from src.optimizer.drive_candidate_ranker import BaseDispatchStrategy

        strategy = BaseDispatchStrategy({}, {}, {})
        drive = Drive(
            uid="low_score_match",
            quality="Gold",
            area=4,
            shape_id="S1",
            set_name="Set",
            main_stats={"m1": 1, "m2": 1},
            sub_stats={"Crit": 1},
            role_scores={"A": 10.0},
        )

        self.assertEqual(
            10.0,
            strategy._rank_score_for_drive("A", drive, 10.0, {"stats": ["Crit"]}),
        )
        self.assertGreater(
            strategy._rank_score_for_drive("A", drive, 10.0, {"stats": ["Crit"], "ignore_grade_limit": True}),
            10.0,
        )

    def test_selected_stat_matching_distinguishes_percent_and_flat_attack(self):
        from src.models.equipment import Drive
        from src.optimizer.scoring import ScoringEngine
        from src.optimizer.drive_candidate_ranker import BaseDispatchStrategy

        flat = Drive(
            uid="flat_attack",
            quality="Gold",
            area=1,
            shape_id="S1",
            set_name="Set",
            main_stats={"m1": 1, "m2": 1},
            sub_stats={"攻击力": 1},
            role_scores={"A": 10.0},
        )
        percent = Drive(
            uid="percent_attack",
            quality="Gold",
            area=1,
            shape_id="S1",
            set_name="Set",
            main_stats={"m1": 1, "m2": 1},
            sub_stats={"攻击力%": 1},
            role_scores={"A": 10.0},
        )

        strategy = BaseDispatchStrategy({}, {}, {})
        scoring = ScoringEngine()
        for matcher in (strategy, scoring):
            self.assertTrue(matcher._item_has_stat(percent, "攻击力%"))
            self.assertFalse(matcher._item_has_stat(flat, "攻击力%"))
            self.assertTrue(matcher._item_has_stat(flat, "攻击力"))
            self.assertFalse(matcher._item_has_stat(percent, "攻击力"))
            self.assertTrue(matcher._item_has_stat(percent, "大攻击"))
            self.assertFalse(matcher._item_has_stat(flat, "大攻击"))
            self.assertTrue(matcher._item_has_stat(flat, "小攻击"))
            self.assertFalse(matcher._item_has_stat(percent, "小攻击"))

        selected_both = {"stats": ["攻击力%", "攻击力"], "ignore_grade_limit": True}
        self.assertTrue(strategy._matches_stat_priority_pool(percent, selected_both))
        self.assertTrue(strategy._matches_stat_priority_pool(flat, selected_both))

    def test_role_priority_ignore_grade_limit_falls_back_when_shape_has_no_selected_stat(self):
        from src.models.equipment import Drive
        from src.optimizer.role_priority_strategy import RolePriorityStrategy

        strategy = RolePriorityStrategy(
            roles_db={"A": {"default_set": "Set"}},
            sets_db={"Set": {"shapes": []}},
            blueprints_db={"A": [{"set_pieces": [], "extra_pieces": ["X", "Y"], "board": []}]},
        )
        matching = Drive(
            uid="matching_x",
            quality="Gold",
            area=1,
            shape_id="X",
            set_name="Set",
            main_stats={"m1": 1, "m2": 1},
            sub_stats={"Crit": 1},
            role_scores={"A": 1.0},
        )
        non_matching = Drive(
            uid="non_matching_y",
            quality="Gold",
            area=1,
            shape_id="Y",
            set_name="Set",
            main_stats={"m1": 1, "m2": 1},
            sub_stats={"H1": 1},
            role_scores={"A": 100.0},
        )

        result = strategy.execute(
            {"drives": [matching, non_matching], "tapes": {"A": []}},
            ["A"],
            {},
            crit_priority_modes={"A": {"stats": ["Crit"], "ignore_grade_limit": True}},
        )

        self.assertTrue(result["A"]["valid"])
        self.assertEqual(
            ["matching_x", "non_matching_y"],
            [drive.uid for drive in result["A"]["assigned_extra_drives"]],
        )

    def test_role_priority_ignore_grade_limit_prefers_plan_with_more_selected_stats(self):
        from src.models.equipment import Drive
        from src.optimizer.role_priority_strategy import RolePriorityStrategy

        strategy = RolePriorityStrategy(
            roles_db={"A": {"default_set": "Set"}},
            sets_db={"Set": {"shapes": []}},
            blueprints_db={
                "A": [
                    {"set_pieces": [], "extra_pieces": ["X", "Y"], "board": []},
                    {"set_pieces": [], "extra_pieces": ["X", "Z"], "board": []},
                ]
            },
        )
        selected_x = Drive(
            uid="selected_x",
            quality="Gold",
            area=1,
            shape_id="X",
            set_name="Set",
            main_stats={"m1": 1, "m2": 1},
            sub_stats={"Crit": 1},
            role_scores={"A": 1.0},
        )
        high_score_y = Drive(
            uid="high_score_y",
            quality="Gold",
            area=1,
            shape_id="Y",
            set_name="Set",
            main_stats={"m1": 1, "m2": 1},
            sub_stats={"H1": 1},
            role_scores={"A": 100.0},
        )
        selected_z = Drive(
            uid="selected_z",
            quality="Gold",
            area=1,
            shape_id="Z",
            set_name="Set",
            main_stats={"m1": 1, "m2": 1},
            sub_stats={"Crit": 1},
            role_scores={"A": 1.0},
        )

        result = strategy.execute(
            {"drives": [selected_x, high_score_y, selected_z], "tapes": {"A": []}},
            ["A"],
            {},
            crit_priority_modes={"A": {"stats": ["Crit"], "ignore_grade_limit": True}},
        )

        self.assertTrue(result["A"]["valid"])
        self.assertEqual(
            ["selected_x", "selected_z"],
            [drive.uid for drive in result["A"]["assigned_extra_drives"]],
        )

    def test_role_priority_ignore_grade_limit_uses_ordered_nested_stat_pool(self):
        from src.models.equipment import Drive
        from src.optimizer.role_priority_strategy import RolePriorityStrategy

        strategy = RolePriorityStrategy(
            roles_db={"A": {"default_set": "Set"}},
            sets_db={"Set": {"shapes": []}},
            blueprints_db={
                "A": [
                    {"set_pieces": [], "extra_pieces": ["X", "Y"], "board": []},
                    {"set_pieces": [], "extra_pieces": ["X", "Z"], "board": []},
                ]
            },
        )
        ab_x = Drive(
            uid="ab_x",
            quality="Gold",
            area=1,
            shape_id="X",
            set_name="Set",
            main_stats={"m1": 1, "m2": 1},
            sub_stats={"AStat": 1, "BStat": 1},
            role_scores={"A": 1.0},
        )
        a_y = Drive(
            uid="a_y",
            quality="Gold",
            area=1,
            shape_id="Y",
            set_name="Set",
            main_stats={"m1": 1, "m2": 1},
            sub_stats={"AStat": 1},
            role_scores={"A": 1.0},
        )
        high_a_x = Drive(
            uid="high_a_x",
            quality="Gold",
            area=1,
            shape_id="X",
            set_name="Set",
            main_stats={"m1": 1, "m2": 1},
            sub_stats={"AStat": 1},
            role_scores={"A": 100.0},
        )
        b_z = Drive(
            uid="b_z",
            quality="Gold",
            area=1,
            shape_id="Z",
            set_name="Set",
            main_stats={"m1": 1, "m2": 1},
            sub_stats={"BStat": 1},
            role_scores={"A": 100.0},
        )

        result = strategy.execute(
            {"drives": [ab_x, a_y, high_a_x, b_z], "tapes": {"A": []}},
            ["A"],
            {},
            crit_priority_modes={"A": {"stats": ["AStat", "BStat"], "ignore_grade_limit": True}},
        )

        self.assertTrue(result["A"]["valid"])
        self.assertEqual(
            ["ab_x", "a_y"],
            [drive.uid for drive in result["A"]["assigned_extra_drives"]],
        )

    def test_role_priority_ignore_grade_limit_single_stat_prefers_more_matching_slots(self):
        from src.models.equipment import Drive
        from src.optimizer.role_priority_strategy import RolePriorityStrategy

        strategy = RolePriorityStrategy(
            roles_db={"A": {"default_set": "Set"}},
            sets_db={"Set": {"shapes": []}},
            blueprints_db={
                "A": [
                    {"set_pieces": [], "extra_pieces": ["X", "Y"], "board": []},
                    {"set_pieces": [], "extra_pieces": ["X", "Z"], "board": []},
                ]
            },
        )
        crit_x = Drive(
            uid="crit_x",
            quality="Gold",
            area=1,
            shape_id="X",
            set_name="Set",
            main_stats={"m1": 1, "m2": 1},
            sub_stats={"Crit": 1},
            role_scores={"A": 1.0},
        )
        crit_y = Drive(
            uid="crit_y",
            quality="Gold",
            area=1,
            shape_id="Y",
            set_name="Set",
            main_stats={"m1": 1, "m2": 1},
            sub_stats={"Crit": 1},
            role_scores={"A": 1.0},
        )
        high_x = Drive(
            uid="high_x",
            quality="Gold",
            area=1,
            shape_id="X",
            set_name="Set",
            main_stats={"m1": 1, "m2": 1},
            sub_stats={"Other": 1},
            role_scores={"A": 100.0},
        )
        high_z = Drive(
            uid="high_z",
            quality="Gold",
            area=1,
            shape_id="Z",
            set_name="Set",
            main_stats={"m1": 1, "m2": 1},
            sub_stats={"Other": 1},
            role_scores={"A": 100.0},
        )

        result = strategy.execute(
            {"drives": [crit_x, crit_y, high_x, high_z], "tapes": {"A": []}},
            ["A"],
            {},
            crit_priority_modes={"A": {"stats": ["Crit"], "ignore_grade_limit": True}},
        )

        self.assertTrue(result["A"]["valid"])
        self.assertEqual(
            ["crit_x", "crit_y"],
            [drive.uid for drive in result["A"]["assigned_extra_drives"]],
        )

    def test_role_priority_equal_priority_without_ignore_keeps_a_grade_gate(self):
        from src.models.equipment import Drive
        from src.optimizer.role_priority_strategy import RolePriorityStrategy

        strategy = RolePriorityStrategy(
            roles_db={"A": {"default_set": "Set"}},
            sets_db={"Set": {"shapes": []}},
            blueprints_db={"A": [{"set_pieces": [], "extra_pieces": ["X"], "board": []}]},
        )
        low_selected = Drive(
            uid="low_selected",
            quality="Gold",
            area=1,
            shape_id="X",
            set_name="Set",
            main_stats={"m1": 1, "m2": 1},
            sub_stats={"Crit": 1},
            role_scores={"A": 1.0},
        )
        high_other = Drive(
            uid="high_other",
            quality="Gold",
            area=1,
            shape_id="X",
            set_name="Set",
            main_stats={"m1": 1, "m2": 1},
            sub_stats={"Other": 1},
            role_scores={"A": 100.0},
        )

        result = strategy.execute(
            {"drives": [low_selected, high_other], "tapes": {"A": []}},
            ["A"],
            {},
            crit_priority_modes={"A": {"stats": ["Crit"], "equal_priority": True}},
        )

        self.assertTrue(result["A"]["valid"])
        self.assertEqual("high_other", result["A"]["assigned_extra_drives"][0].uid)

    def test_role_priority_equal_group_balances_selected_stat_coverage(self):
        from src.models.equipment import Drive
        from src.optimizer.role_priority_strategy import RolePriorityStrategy

        strategy = RolePriorityStrategy(
            roles_db={"A": {"default_set": "Set"}, "B": {"default_set": "Set"}},
            sets_db={"Set": {"shapes": []}},
            blueprints_db={
                "A": [{"set_pieces": [], "extra_pieces": ["X", "Y"], "board": []}],
                "B": [{"set_pieces": [], "extra_pieces": ["X", "Y"], "board": []}],
            },
        )
        x_crit = Drive(
            uid="x_crit",
            quality="Gold",
            area=1,
            shape_id="X",
            set_name="Set",
            main_stats={"m1": 1, "m2": 1},
            sub_stats={"Crit": 1},
            role_scores={"A": 100.0, "B": 90.0},
        )
        y_crit = Drive(
            uid="y_crit",
            quality="Gold",
            area=1,
            shape_id="Y",
            set_name="Set",
            main_stats={"m1": 1, "m2": 1},
            sub_stats={"Crit": 1},
            role_scores={"A": 100.0, "B": 1.0},
        )
        x_other = Drive(
            uid="x_other",
            quality="Gold",
            area=1,
            shape_id="X",
            set_name="Set",
            main_stats={"m1": 1, "m2": 1},
            sub_stats={"Other": 1},
            role_scores={"A": 1.0, "B": 80.0},
        )
        y_other = Drive(
            uid="y_other",
            quality="Gold",
            area=1,
            shape_id="Y",
            set_name="Set",
            main_stats={"m1": 1, "m2": 1},
            sub_stats={"Other": 1},
            role_scores={"A": 1.0, "B": 80.0},
        )

        result = strategy.execute(
            {"drives": [x_crit, y_crit, x_other, y_other], "tapes": {"A": [], "B": []}},
            ["A", "B"],
            {},
            crit_priority_modes={
                "A": {"stats": ["Crit"], "equal_priority": True, "ignore_grade_limit": True},
                "B": {"stats": ["Crit"], "equal_priority": True, "ignore_grade_limit": True},
            },
            priority_groups=[["A", "B"]],
        )

        self.assertTrue(result["A"]["valid"])
        self.assertTrue(result["B"]["valid"])
        self.assertEqual(
            {"y_crit"},
            {drive.uid for drive in result["A"]["assigned_extra_drives"] if "Crit" in drive.sub_stats},
        )
        self.assertEqual(
            {"x_crit"},
            {drive.uid for drive in result["B"]["assigned_extra_drives"] if "Crit" in drive.sub_stats},
        )

    def test_grouped_role_priority_assigns_zero_score_tapes_from_filtered_pool(self):
        from src.models.equipment import Tape
        from src.optimizer.role_priority_strategy import RolePriorityStrategy

        strategy = RolePriorityStrategy(
            roles_db={
                "A": {"default_set": "Set"},
                "B": {"default_set": "Set"},
            },
            sets_db={"Set": {"shapes": []}},
            blueprints_db={},
        )
        tape_a = Tape(uid="tape_a", quality="Gold", area=15, set_name="Set", main_stats="Wanted", sub_stats={})
        tape_b = Tape(uid="tape_b", quality="Gold", area=15, set_name="Set", main_stats="Wanted", sub_stats={})
        tape_a.role_scores = {"A": 0.0, "B": 0.0}
        tape_b.role_scores = {"A": 0.0, "B": 0.0}

        assigned = strategy._pre_allocate_tapes_for_groups(
            [["A", "B"]],
            {},
            {"A": [tape_a], "B": [tape_b]},
            {},
        )

        self.assertEqual(
            {"A": "tape_a", "B": "tape_b"},
            {role: tape.uid if tape else None for role, tape in assigned.items()},
        )

    def test_grouped_role_priority_does_not_share_filtered_tapes_across_roles(self):
        from src.models.equipment import Tape
        from src.optimizer.role_priority_strategy import RolePriorityStrategy

        strategy = RolePriorityStrategy(
            roles_db={
                "A": {"default_set": "Set"},
                "B": {"default_set": "Set"},
            },
            sets_db={"Set": {"shapes": []}},
            blueprints_db={},
        )
        tape_a = Tape(uid="tape_a", quality="Gold", area=15, set_name="Set", main_stats="Wanted", sub_stats={})
        tape_a.role_scores = {"A": 0.0, "B": 0.0}

        assigned = strategy._pre_allocate_tapes_for_groups(
            [["A", "B"]],
            {},
            {"A": [tape_a], "B": []},
            {},
        )

        self.assertEqual("tape_a", assigned["A"].uid)
        self.assertIsNone(assigned["B"])

    def test_role_preference_configs_are_rejected_for_score_based_modes(self):
        from src.features.allocation.preference_modes import role_preference_mode_error

        self.assertIsNone(role_preference_mode_error("role_priority", {"A": ["Wanted"]}, {}))
        self.assertIsNone(role_preference_mode_error("update_mode", {}, {"A": {"stats": ["Crit"]}}))
        self.assertIn("词条自选", role_preference_mode_error("drive_priority", {"A": ["Wanted"]}, {}))
        self.assertIn("词条自选", role_preference_mode_error("global_optimal", {}, {"A": {"stats": ["Crit"]}}))

    def test_role_preference_configs_reject_crit_rate_caps_for_score_based_modes(self):
        from src.features.allocation.preference_modes import role_preference_mode_error

        self.assertIsNone(role_preference_mode_error("role_priority", {}, {}, {"A": 76.0}))
        self.assertIsNone(role_preference_mode_error("update_mode", {}, {}, {"A": 76.0}))
        self.assertIn("暴击率上限", role_preference_mode_error("drive_priority", {}, {}, {"A": 76.0}))
        self.assertIn("暴击率上限", role_preference_mode_error("global_optimal", {}, {}, {"A": 76.0}))

    def test_role_priority_respects_crit_rate_cap_when_selecting_drives(self):
        from src.models.equipment import Drive
        from src.optimizer.role_priority_strategy import RolePriorityStrategy

        strategy = RolePriorityStrategy(
            roles_db={"A": {"default_set": "Set", "weights": {"攻击力": 1.0, "暴击率%": 1.0}}},
            sets_db={"Set": {"shapes": []}},
            blueprints_db={"A": [{"set_pieces": [], "extra_pieces": ["H_2"], "board": [[1]]}]},
        )
        high = Drive(
            uid="high_crit",
            quality="Gold",
            area=2,
            shape_id="H_2",
            set_name="Set",
            main_stats={"攻击力": 1.0, "生命值": 1.0},
            sub_stats={"暴击率%": 20.0},
            role_scores={"A": 20.0},
        )
        safe = Drive(
            uid="safe",
            quality="Gold",
            area=2,
            shape_id="H_2",
            set_name="Set",
            main_stats={"攻击力": 1.0, "生命值": 1.0},
            sub_stats={"攻击力": 1.0},
            role_scores={"A": 5.0},
        )

        result = strategy.execute(
            {"drives": [high, safe], "tapes": {"A": []}},
            ["A"],
            {},
            crit_priority_modes={},
            crit_rate_caps={"A": 10.0},
        )

        self.assertTrue(result["A"]["valid"])
        self.assertEqual("safe", result["A"]["assigned_extra_drives"][0].uid)

    def test_role_priority_counts_extra_shape_crit_buff_in_crit_rate_cap(self):
        from src.models.equipment import Drive
        from src.optimizer.role_priority_strategy import RolePriorityStrategy

        strategy = RolePriorityStrategy(
            roles_db={
                "A": {
                    "default_set": "Set",
                    "weights": {"攻击力": 1.0, "暴击率%": 1.0},
                    "extra_shape_label": "2型",
                    "extra_shape_buffs": {"暴击率%": 15.0},
                }
            },
            sets_db={"Set": {"shapes": []}},
            blueprints_db={"A": [{"set_pieces": [], "extra_pieces": ["H_2"], "board": [[1]]}]},
        )
        buffed = Drive(
            uid="buffed",
            quality="Gold",
            area=2,
            shape_id="H_2",
            set_name="Set",
            main_stats={"攻击力": 1.0, "生命值": 1.0},
            sub_stats={},
            role_scores={"A": 20.0},
        )
        fallback = Drive(
            uid="fallback",
            quality="Gold",
            area=3,
            shape_id="H_2",
            set_name="Set",
            main_stats={"攻击力": 1.0, "生命值": 1.0},
            sub_stats={},
            role_scores={"A": 5.0},
        )

        result = strategy.execute(
            {"drives": [buffed, fallback], "tapes": {"A": []}},
            ["A"],
            {},
            crit_priority_modes={},
            crit_rate_caps={"A": 10.0},
        )

        self.assertTrue(result["A"]["valid"])
        self.assertEqual("fallback", result["A"]["assigned_extra_drives"][0].uid)

    def test_role_priority_uses_extra_shape_buff_for_two_piece_set_choice(self):
        from src.models.equipment import Drive
        from src.optimizer.role_priority_strategy import RolePriorityStrategy

        strategy = RolePriorityStrategy(
            roles_db={
                "A": {
                    "default_set": "Set",
                    "weights": {"攻击力%": 1.0},
                    "extra_shape_label": "2型",
                    "extra_shape_buffs": {"攻击力%": 10.0},
                }
            },
            sets_db={"Set": {"shapes": []}},
            blueprints_db={
                "A": [
                    {"set_pieces": ["H_2"], "extra_pieces": [], "set_effect_mode": "two_piece", "board": [[1]]},
                    {"set_pieces": ["H_3"], "extra_pieces": [], "set_effect_mode": "two_piece", "board": [[1]]},
                ]
            },
        )
        strategy.stat_catalog.gold_base_values["攻击力%"] = 1.25
        hidden_better = Drive(
            uid="hidden_better",
            quality="Gold",
            area=2,
            shape_id="H_2",
            set_name="Set",
            main_stats={"攻击力": 1.0, "生命值": 1.0},
            sub_stats={},
            role_scores={"A": 1.0},
        )
        visible_better = Drive(
            uid="visible_better",
            quality="Gold",
            area=3,
            shape_id="H_3",
            set_name="Set",
            main_stats={"攻击力": 1.0, "生命值": 1.0},
            sub_stats={},
            role_scores={"A": 20.0},
        )

        result = strategy.execute(
            {"drives": [visible_better, hidden_better], "tapes": {"A": []}},
            ["A"],
            {},
            crit_priority_modes={},
        )

        self.assertTrue(result["A"]["valid"])
        self.assertEqual("hidden_better", result["A"]["assigned_set_drives"][0].uid)
        self.assertEqual(1.0, result["A"]["score"])
        self.assertNotIn("rank_score", result["A"])

    def test_drive_priority_uses_extra_shape_buff_for_two_piece_set_choice(self):
        from src.models.equipment import Drive
        from src.optimizer.drive_priority_strategy import DrivePriorityStrategy

        strategy = DrivePriorityStrategy(
            roles_db={
                "A": {
                    "default_set": "Set",
                    "weights": {"攻击力%": 1.0},
                    "extra_shape_label": "2型",
                    "extra_shape_buffs": {"攻击力%": 10.0},
                }
            },
            sets_db={"Set": {"shapes": []}},
            blueprints_db={
                "A": [
                    {"set_pieces": ["H_2"], "extra_pieces": [], "set_effect_mode": "two_piece", "board": [[1]]},
                    {"set_pieces": ["H_3"], "extra_pieces": [], "set_effect_mode": "two_piece", "board": [[1]]},
                ]
            },
        )
        strategy.stat_catalog.gold_base_values["攻击力%"] = 1.25
        hidden_better = Drive(
            uid="hidden_better",
            quality="Gold",
            area=2,
            shape_id="H_2",
            set_name="Set",
            main_stats={"攻击力": 1.0, "生命值": 1.0},
            sub_stats={},
            role_scores={"A": 1.0},
        )
        visible_better = Drive(
            uid="visible_better",
            quality="Gold",
            area=3,
            shape_id="H_3",
            set_name="Set",
            main_stats={"攻击力": 1.0, "生命值": 1.0},
            sub_stats={},
            role_scores={"A": 20.0},
        )

        result = strategy.execute(
            {"drives": [visible_better, hidden_better], "tapes": {"A": []}},
            ["A"],
            {},
            crit_priority_modes={},
        )

        self.assertTrue(result["A"]["valid"])
        self.assertEqual("hidden_better", result["A"]["assigned_set_drives"][0].uid)
        self.assertEqual(1.0, result["A"]["score"])

    def test_role_priority_does_not_use_extra_shape_buff_for_extra_fill_slots(self):
        from src.models.equipment import Drive
        from src.optimizer.role_priority_strategy import RolePriorityStrategy

        strategy = RolePriorityStrategy(
            roles_db={
                "A": {
                    "default_set": "Set",
                    "weights": {"攻击力%": 1.0},
                    "extra_shape_label": "2型",
                    "extra_shape_buffs": {"攻击力%": 10.0},
                }
            },
            sets_db={"Set": {"shapes": []}},
            blueprints_db={
                "A": [
                    {"set_pieces": [], "extra_pieces": ["H_2"], "set_effect_mode": "none", "board": [[1]]},
                    {"set_pieces": [], "extra_pieces": ["H_3"], "set_effect_mode": "none", "board": [[1]]},
                ]
            },
        )
        strategy.stat_catalog.gold_base_values["攻击力%"] = 1.25
        hidden_shape = Drive(
            uid="hidden_shape",
            quality="Gold",
            area=2,
            shape_id="H_2",
            set_name="Set",
            main_stats={"攻击力": 1.0, "生命值": 1.0},
            sub_stats={},
            role_scores={"A": 1.0},
        )
        visible_better = Drive(
            uid="visible_better",
            quality="Gold",
            area=3,
            shape_id="H_3",
            set_name="Set",
            main_stats={"攻击力": 1.0, "生命值": 1.0},
            sub_stats={},
            role_scores={"A": 20.0},
        )

        result = strategy.execute(
            {"drives": [visible_better, hidden_shape], "tapes": {"A": []}},
            ["A"],
            {},
            crit_priority_modes={},
        )

        self.assertTrue(result["A"]["valid"])
        self.assertEqual("visible_better", result["A"]["assigned_extra_drives"][0].uid)
        self.assertEqual(20.0, result["A"]["score"])

    def test_global_optimal_ignores_extra_shape_hidden_ranking_score(self):
        from src.models.equipment import Drive
        from src.optimizer.drive_priority_strategy import GlobalOptimalStrategy

        strategy = GlobalOptimalStrategy(
            roles_db={
                "A": {
                    "default_set": "Set",
                    "weights": {"攻击力%": 1.0},
                    "extra_shape_label": "2型",
                    "extra_shape_buffs": {"攻击力%": 10.0},
                }
            },
            sets_db={"Set": {"shapes": []}},
            blueprints_db={
                "A": [
                    {"set_pieces": ["H_2"], "extra_pieces": [], "set_effect_mode": "two_piece", "board": [[1]]},
                    {"set_pieces": ["H_3"], "extra_pieces": [], "set_effect_mode": "two_piece", "board": [[1]]},
                ]
            },
        )
        strategy.stat_catalog.gold_base_values["攻击力%"] = 1.25
        hidden_better = Drive(
            uid="hidden_better",
            quality="Gold",
            area=2,
            shape_id="H_2",
            set_name="Set",
            main_stats={"攻击力": 1.0, "生命值": 1.0},
            sub_stats={},
            role_scores={"A": 1.0},
        )
        visible_better = Drive(
            uid="visible_better",
            quality="Gold",
            area=3,
            shape_id="H_3",
            set_name="Set",
            main_stats={"攻击力": 1.0, "生命值": 1.0},
            sub_stats={},
            role_scores={"A": 20.0},
        )

        result = strategy.execute(
            {"drives": [visible_better, hidden_better], "tapes": {"A": []}},
            ["A"],
            {},
            crit_priority_modes={},
        )

        self.assertTrue(result["A"]["valid"])
        self.assertEqual("visible_better", result["A"]["assigned_set_drives"][0].uid)
        self.assertEqual(20.0, result["A"]["score"])

    def test_dispatcher_drops_stat_priority_for_score_based_modes(self):
        from src.optimizer.dispatcher import DispatcherEngine

        class FakeStrategy:
            def __init__(self):
                self.received_config = None

            def execute(self, _pool, _priority, _sets, crit_priority_modes):
                self.received_config = crit_priority_modes
                return None

        strategy = FakeStrategy()
        dispatcher = object.__new__(DispatcherEngine)
        dispatcher.strategies = {"drive_priority": strategy}

        dispatcher.execute_dispatch(
            "drive_priority",
            candidate_pool={},
            priority_list=["A"],
            crit_priority_modes={"A": {"stats": ["Crit"], "ignore_grade_limit": True}},
        )

        self.assertEqual({}, strategy.received_config)

    def test_orchestrator_reads_largest_priority_group_size(self):
        from src.solver.orchestrator import NTEPipelineOrchestrator

        orchestrator = object.__new__(NTEPipelineOrchestrator)

        self.assertEqual(1, orchestrator._max_priority_group_size(["A", "B"], None))
        self.assertEqual(
            3,
            orchestrator._max_priority_group_size(
                ["A", "B", "C", "D"],
                [["A", "B", "C"], ["D"]],
            ),
        )


class OfflineParseWorkflowTests(unittest.TestCase):
    def test_all_offline_scope_replaces_inventory(self):
        from src.features.scanning.controller import offline_scope_replaces_inventory

        self.assertTrue(offline_scope_replaces_inventory("all"))
        self.assertTrue(offline_scope_replaces_inventory("full"))
        self.assertFalse(offline_scope_replaces_inventory("incremental"))


class ConfigDraftWorkflowTests(unittest.TestCase):
    def test_config_form_changes_are_draft_until_save_button(self):
        from src.features.configuration import page as config_page

        class Window:
            _current_config_name = "roles.json"

            def __init__(self):
                self.loaded = False

            def _load_data(self):
                self.loaded = True

        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp)
            path = config_dir / "roles.json"
            path.write_text(json.dumps({"Old": {"weights": {}}}, ensure_ascii=False), encoding="utf-8")
            window = Window()

            config_page.save_config_data(window, {"New": {"weights": {}}}, config_dir)
            self.assertEqual({"Old": {"weights": {}}}, json.loads(path.read_text(encoding="utf-8")))
            self.assertTrue(window._config_dirty)

            original_information = config_page.QMessageBox.information
            config_page.QMessageBox.information = lambda *_args, **_kwargs: None
            try:
                config_page.save_config_form(window, config_dir, None)
            finally:
                config_page.QMessageBox.information = original_information
            self.assertEqual({"New": {"weights": {}}}, json.loads(path.read_text(encoding="utf-8")))
            self.assertFalse(window._config_dirty)
            self.assertTrue(window.loaded)

    def test_config_loader_reads_file_every_time_when_not_dirty(self):
        from src.features.configuration.page import load_config_data

        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp)
            path = config_dir / "roles.json"
            path.write_text(json.dumps({"A": {}}, ensure_ascii=False), encoding="utf-8")
            self.assertEqual({"A": {}}, load_config_data("roles.json", config_dir))

            path.write_text(json.dumps({"B": {}}, ensure_ascii=False), encoding="utf-8")
            self.assertEqual({"B": {}}, load_config_data("roles.json", config_dir))

    def test_roles_form_lazily_builds_role_tabs(self):
        from PySide6.QtWidgets import QApplication, QTabWidget, QVBoxLayout, QWidget

        from src.features.configuration import page as config_page

        app = QApplication.instance() or QApplication([])

        class Window:
            all_set_names = ["套装A"]

            def __init__(self):
                self.container = QWidget()
                self.config_form_layout = QVBoxLayout(self.container)

            def _stat_choice_pool(self):
                return ["攻击力"]

            def _save_role_field(self, *_args):
                pass

            def _save_single_extra_shape_buff(self, *_args):
                pass

            def _save_role_weight_value(self, *_args):
                pass

            def _del_role(self, *_args):
                pass

            def _add_weight(self, *_args):
                pass

            def _del_weight(self, *_args):
                pass

        data = {
            "A": {"default_set": "套装A", "extra_shape_buffs": {}, "board_matrix": [[0] * 5 for _ in range(5)], "weights": {}},
            "B": {"default_set": "套装A", "extra_shape_buffs": {}, "board_matrix": [[0] * 5 for _ in range(5)], "weights": {}},
        }
        window = Window()
        config_page.render_roles_form(window, data)
        tabs = window.container.findChild(QTabWidget)

        self.assertIsNotNone(tabs)
        self.assertTrue(tabs.widget(0).property("loaded"))
        self.assertFalse(tabs.widget(1).property("loaded"))

        tabs.setCurrentIndex(1)
        app.processEvents()

        self.assertTrue(tabs.widget(1).property("loaded"))

    def test_roles_form_shows_sub_and_tape_main_weights_separately(self):
        from PySide6.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget

        from src.features.configuration import page as config_page

        app = QApplication.instance() or QApplication([])

        class Window:
            all_set_names = ["套装A"]

            def __init__(self):
                self.container = QWidget()
                self.config_form_layout = QVBoxLayout(self.container)

            def _stat_choice_pool(self):
                return ["攻击力"]

            def _save_role_field(self, *_args):
                pass

            def _save_single_extra_shape_buff(self, *_args):
                pass

            def _save_role_weight_value(self, *_args):
                pass

            def _del_role(self, *_args):
                pass

            def _add_weight(self, *_args):
                pass

            def _del_weight(self, *_args):
                pass

        data = {
            "A": {
                "default_set": "套装A",
                "extra_shape_buffs": {},
                "board_matrix": [[0] * 5 for _ in range(5)],
                "weights": {"攻击力": 0.4},
                "main_weights": {"攻击力%": 0.9},
            }
        }
        window = Window()
        config_page.render_roles_form(window, data)
        app.processEvents()

        texts = [label.text() for label in window.container.findChildren(QLabel)]
        self.assertIn("副词条权重:", texts)
        self.assertIn("卡带主词条权重:", texts)
        self.assertIn("攻击力", texts)
        self.assertIn("攻击力%", texts)

    def test_role_weight_helpers_write_selected_weight_field(self):
        from unittest.mock import patch

        from src.features.configuration import page as config_page

        data = {"A": {"weights": {"攻击力": 0.4}, "main_weights": {"攻击力%": 0.9}}}
        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp)

            with patch.object(config_page, "save_config_data") as save_mock:
                config_page.save_role_weight_value(None, "A", "攻击力%", 1.2, data, config_dir, "main_weights")
                self.assertEqual(1.2, data["A"]["main_weights"]["攻击力%"])
                self.assertEqual(0.4, data["A"]["weights"]["攻击力"])
                save_mock.assert_called_once()

            with patch.object(config_page, "save_config_data") as save_mock:
                config_page.del_weight(None, "A", "攻击力%", data, lambda: None, config_dir, "main_weights")
                self.assertNotIn("攻击力%", data["A"]["main_weights"])
                self.assertIn("攻击力", data["A"]["weights"])
                save_mock.assert_called_once()

    def test_roles_form_can_open_newly_added_role_tab(self):
        from PySide6.QtWidgets import QApplication, QTabWidget, QVBoxLayout, QWidget

        from src.features.configuration import page as config_page

        app = QApplication.instance() or QApplication([])

        class Window:
            all_set_names = ["套装A"]

            def __init__(self):
                self.container = QWidget()
                self.config_form_layout = QVBoxLayout(self.container)

            def _stat_choice_pool(self):
                return ["攻击力"]

            def _save_role_field(self, *_args):
                pass

            def _save_single_extra_shape_buff(self, *_args):
                pass

            def _save_role_weight_value(self, *_args):
                pass

            def _del_role(self, *_args):
                pass

            def _add_weight(self, *_args):
                pass

            def _del_weight(self, *_args):
                pass

        data = {
            "A": {"default_set": "套装A", "extra_shape_buffs": {}, "board_matrix": [[0] * 5 for _ in range(5)], "weights": {}},
            "新角色": {"default_set": "套装A", "extra_shape_buffs": {}, "board_matrix": [[0] * 5 for _ in range(5)], "weights": {}},
        }
        window = Window()
        config_page.render_roles_form(window, data, active_role="新角色")
        tabs = window.container.findChild(QTabWidget)

        self.assertEqual("新角色", tabs.tabText(tabs.currentIndex()))
        app.processEvents()

    def test_confirm_pending_config_changes_can_cancel_navigation(self):
        from src.features.configuration import page as config_page

        class Window:
            _current_config_name = "roles.json"
            _config_dirty = True

        original_question = config_page.QMessageBox.question
        config_page.QMessageBox.question = lambda *_args, **_kwargs: config_page.QMessageBox.Cancel
        try:
            self.assertFalse(config_page.confirm_pending_config_changes(Window(), Path(".")))
        finally:
            config_page.QMessageBox.question = original_question


class AccountTransferWorkflowTests(unittest.TestCase):
    def _make_manager(self, root: Path):
        from src.features.accounts.manager import AccountManager

        return AccountManager(
            data_root=root,
            bundled_config_dir=root / "bundled",
            iter_image_files=lambda path: [p for p in path.rglob("*") if p.is_file()],
            core_config_files=("roles.json", "sets.json", "stats.json", "shapes.json"),
            account_user_files=("equipped_state.json", "real_inventory.json", "priority_config.json"),
        )

    def test_export_current_account_includes_only_baseline_screenshot(self):
        from src.features.accounts.manager import export_account_data

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manager = self._make_manager(root)
            account_id = manager.create_account("Main")
            account_root = manager.account_dir(account_id)
            (account_root / "config" / "real_inventory.json").write_text("[1]", encoding="utf-8")
            (account_root / "scanned_images" / "raw_drive_0001.png").write_bytes(b"baseline")
            (account_root / "scanned_images" / "raw_drive_0002.png").write_bytes(b"extra")

            zip_path = root / "main-export.zip"
            export_account_data(manager, account_id, zip_path)

            with zipfile.ZipFile(zip_path) as zf:
                names = set(zf.namelist())
            self.assertIn("manifest.json", names)
            self.assertIn("account/config/real_inventory.json", names)
            self.assertIn("account/scanned_images/raw_drive_0001.png", names)
            self.assertNotIn("account/scanned_images/raw_drive_0002.png", names)

    def test_import_account_with_same_name_replaces_existing_account(self):
        from src.features.accounts.manager import export_account_data, import_account_data

        with tempfile.TemporaryDirectory() as src_tmp, tempfile.TemporaryDirectory() as dst_tmp:
            src_root = Path(src_tmp)
            src_manager = self._make_manager(src_root)
            src_id = src_manager.create_account("Main")
            (src_manager.account_dir(src_id) / "config" / "real_inventory.json").write_text(
                "[{\"uid\":\"new\"}]", encoding="utf-8"
            )
            export_path = src_root / "main.zip"
            export_account_data(src_manager, src_id, export_path)

            dst_root = Path(dst_tmp)
            dst_manager = self._make_manager(dst_root)
            dst_id = dst_manager.create_account("Main")
            (dst_manager.account_dir(dst_id) / "config" / "real_inventory.json").write_text(
                "[{\"uid\":\"old\"}]", encoding="utf-8"
            )

            imported_id = import_account_data(dst_manager, export_path)
            imported_inventory = json.loads(
                (dst_manager.account_dir(imported_id) / "config" / "real_inventory.json").read_text(encoding="utf-8")
            )

            self.assertEqual(dst_id, imported_id)
            self.assertEqual([{"uid": "new"}], imported_inventory)


