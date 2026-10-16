# 测试全量扫描与截图解析的流水线执行逻辑。
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from unittest.mock import Mock, patch

from src.domain.post_actions import summarize_state_changes
from src.domain.equipment.matching import EquipmentMatchContext, RoleSuitMatch


def _fake_state_sync_factory(scanner, *, total_drives, post_action_ready_callback, sleep_fn, check_current=None):
    def sync(state_changes, config):
        if not state_changes:
            return summarize_state_changes([])
        if post_action_ready_callback is not None:
            post_action_ready_callback()
        if check_current is not None:
            check_current()
        return scanner.sync_equipment_states(total_drives, state_changes, action_mode="default")

    return SimpleNamespace(sync=sync)


class StreamingScanPipelineTests(unittest.TestCase):
    def test_mouse_worker_honors_bounded_streaming_parse_option(self):
        from src.app.workers import FullVisualScanParseWorkerThread

        mouse = FullVisualScanParseWorkerThread(
            total_drives=1,
            screenshot_dir="screenshots",
            config_dir="config",
            user_database_path="account.sqlite3",
            parse_during_scan=True,
            capture_driver="mouse",
        )
        gamepad = FullVisualScanParseWorkerThread(
            total_drives=1,
            screenshot_dir="screenshots",
            config_dir="config",
            user_database_path="account.sqlite3",
            parse_during_scan=True,
            capture_driver="gamepad",
        )

        self.assertTrue(mouse.parse_during_scan)
        self.assertTrue(gamepad.parse_during_scan)
        self.assertEqual("standard-low-load-v1", mouse.mouse_input_profile)
        self.assertEqual(21, mouse.capture_queue_maxsize)
        self.assertFalse(mouse.low_load_mode)
        self.assertEqual(0.12, mouse.low_load_parse_delay_seconds)

        low_load_mouse = FullVisualScanParseWorkerThread(
            total_drives=1,
            screenshot_dir="screenshots",
            config_dir="config",
            user_database_path="account.sqlite3",
            parse_during_scan=True,
            amd_compatibility=True,
            capture_driver="mouse",
        )
        self.assertFalse(low_load_mouse.parse_during_scan)
        self.assertEqual("compatibility-low-load-v1", low_load_mouse.mouse_input_profile)
        self.assertEqual(7, low_load_mouse.capture_queue_maxsize)
        self.assertTrue(low_load_mouse.low_load_mode)
        self.assertEqual(0.20, low_load_mouse.low_load_parse_delay_seconds)

    def test_gamepad_worker_waits_for_post_action_focus_ack(self):
        from src.app import workers

        worker = workers.GamepadScanParseWorkerThread(
            total_drives=1,
            screenshot_dir="screenshots",
            config_dir="config",
            user_database_path="account.sqlite3",
        )
        events = []
        sleeps = []
        original_sleep = workers.time.sleep

        def on_ready():
            events.append("ready")
            worker.acknowledge_post_actions_ready()

        worker.post_actions_ready.connect(on_ready)
        workers.time.sleep = lambda seconds: sleeps.append(seconds)
        try:
            worker._notify_post_actions_ready()
        finally:
            workers.time.sleep = original_sleep

        self.assertEqual(["ready"], events)
        self.assertEqual([2.0], sleeps)

    def _state_button_image(self, active=None, width=2560, height=1440):
        from src.integrations.vision import equipment_state_detection
        from src.integrations.vision.window_capture import game_content_rect

        img = np.full((height, width, 3), 24, dtype=np.uint8)
        left, top, content_width, content_height = game_content_rect(width, height)
        centers = {
            "discarded": equipment_state_detection.TRASH_BUTTON_CENTER,
            "locked": equipment_state_detection.LOCK_BUTTON_CENTER,
        }
        for state, center in centers.items():
            cx = int(round(left + content_width * center[0]))
            cy = int(round(top + content_height * center[1]))
            value = 188 if state == active else 80
            img[cy - 15 : cy + 15, cx - 15 : cx + 15] = value
        return img

    def test_equipment_state_uses_right_panel_action_buttons(self):
        from src.integrations.vision.equipment_state_detection import right_panel_button_state_from_image

        self.assertEqual("normal", right_panel_button_state_from_image(self._state_button_image()))
        self.assertEqual("locked", right_panel_button_state_from_image(self._state_button_image("locked")))
        self.assertEqual("discarded", right_panel_button_state_from_image(self._state_button_image("discarded")))

    def test_equipment_state_uses_top_aligned_canvas_on_16_10(self):
        from src.integrations.vision.equipment_state_detection import right_panel_button_state_from_image

        image = self._state_button_image("locked", width=2560, height=1600)

        self.assertEqual("locked", right_panel_button_state_from_image(image))

    def test_post_action_thresholds_include_equal_grade(self):
        from src.domain.post_actions import (
            default_post_action_config,
            target_state_for_item,
        )

        class FakeScoring:
            roles_db = {"A": {}, "B": {}}

            def get_grade_tag(self, score, _area):
                return {1.0: "SS", 2.0: "SSS"}.get(float(score), "D")

        item = SimpleNamespace(quality="Gold", area=3, shape_id="H_3", role_scores={"A": 1.0, "B": 1.0})
        context = EquipmentMatchContext(matches=tuple(
            RoleSuitMatch(role, index, "suit", "森林萤火之心", True, frozenset({"H_3"}), frozenset())
            for index, role in enumerate(("A", "B"), 1)))
        config = default_post_action_config()
        config["discard"]["enabled"] = True
        config["discard"]["grade"] = "SS"
        self.assertEqual("discarded", target_state_for_item(item, "normal", config, FakeScoring(), score_context=context))

        item.role_scores = {"A": 2.0, "B": 1.0}
        config = default_post_action_config()
        config["lock"]["enabled"] = True
        config["lock"]["grade"] = "SSS"
        self.assertEqual("locked", target_state_for_item(item, "normal", config, FakeScoring(), score_context=context))

    def test_post_action_preserve_rules_keep_or_lock_matching_tape(self):
        from src.domain.post_actions import default_post_action_config, target_state_for_item
        from src.models.equipment import Tape

        class FakeScoring:
            roles_db = {"A": {}}

            def get_grade_tag(self, _score, _area):
                return "D"

        tape = Tape(
            uid="reserved-tape",
            quality="Purple",
            area=15,
            set_name="森林萤火之心",
            main_stats="攻击力%",
            sub_stats={"暴击率%": 8.0, "暴击伤害%": 16.0},
            role_scores={"A": 0.0},
        )
        rule = {
            "enabled": True,
            "name": "双爆输出卡带",
            "item_type": "tape",
            "action": "keep",
            "main_stats": ["攻击力%"],
            "sub_stats": ["暴击率%", "暴击伤害%"],
            "sub_match": "all",
            "quality_scope": "gold_purple",
            "shape_ids": None,
            "set_names": None,
        }
        config = default_post_action_config()
        config["discard"].update({"enabled": True, "grade": "S"})
        config["preserve_rules"] = [rule]

        self.assertEqual("normal", target_state_for_item(tape, "normal", config, FakeScoring()))
        self.assertEqual("normal", target_state_for_item(tape, "discarded", config, FakeScoring()))

        rule["action"] = "lock"
        self.assertEqual("locked", target_state_for_item(tape, "normal", config, FakeScoring()))

    def test_preserve_rule_sub_stat_match_counts(self):
        from src.domain.post_actions import preserve_rule_matches_item
        from src.models.equipment import Drive

        drive = Drive(
            uid="sub-stat-counts",
            quality="Gold",
            area=3,
            shape_id="H_3",
            main_stats={"攻击力": 1.0, "生命值": 1.0},
            sub_stats={"攻击力%": 1.0, "暴击率%": 2.0},
        )
        rule = {
            "enabled": True,
            "item_type": "drive",
            "quality_scope": "all",
            "shape_ids": ["H_3"],
            "set_names": None,
            "sub_stats": ["攻击力%", "暴击率%", "暴击伤害%"],
        }

        rule["sub_match"] = 1
        self.assertTrue(preserve_rule_matches_item(drive, rule))
        rule["sub_match"] = 2
        self.assertTrue(preserve_rule_matches_item(drive, rule))
        rule["sub_match"] = 3
        self.assertFalse(preserve_rule_matches_item(drive, rule))
        rule["sub_match"] = "all"
        self.assertFalse(preserve_rule_matches_item(drive, rule))

    def test_post_action_type_range_filters_default_drive_shapes_and_tape_sets(self):
        from src.domain.post_actions import (
            default_post_action_config,
            summarize_post_action_filtering,
            target_state_for_item,
        )
        from src.models.equipment import Drive, Tape

        class FakeScoring:
            roles_db = {"A": {}}

            def get_grade_tag(self, score, _area):
                return "SSS" if float(score) >= 2.0 else "D"

        config = default_post_action_config()
        config["lock"]["enabled"] = True
        config["lock"]["grade"] = "SSS"

        keep_drive = Drive(
            uid="keep-drive",
            quality="Gold",
            area=3,
            shape_id="H_3",
            main_stats={"攻击力": 1.0, "生命值": 1.0},
            sub_stats={},
            role_scores={"A": 2.0},
        )
        excluded_drive = Drive(
            uid="excluded-drive",
            quality="Gold",
            area=2,
            shape_id="H_2",
            main_stats={"攻击力": 1.0, "生命值": 1.0},
            sub_stats={},
            role_scores={"A": 2.0},
        )
        keep_tape = Tape(
            uid="keep-tape",
            quality="Gold",
            area=15,
            set_name="森林萤火之心",
            main_stats="攻击力",
            sub_stats={},
            role_scores={"A": 2.0},
        )
        excluded_tape = Tape(
            uid="excluded-tape",
            quality="Gold",
            area=15,
            set_name="音速蓝刺猬",
            main_stats="攻击力",
            sub_stats={},
            role_scores={"A": 2.0},
        )

        context = EquipmentMatchContext(matches=(RoleSuitMatch(
            "A", 1, "suit", "森林萤火之心", True, frozenset({"H_3"}), frozenset()),))
        self.assertEqual("locked", target_state_for_item(keep_drive, "normal", config, FakeScoring(), score_context=context))
        self.assertEqual("normal", target_state_for_item(excluded_drive, "normal", config, FakeScoring(), score_context=context))
        self.assertEqual("locked", target_state_for_item(keep_tape, "normal", config, FakeScoring(), score_context=context))
        self.assertEqual("normal", target_state_for_item(excluded_tape, "normal", config, FakeScoring(), score_context=context))
        self.assertEqual(
            {
                "post_action_parsed_count": 4,
                "post_action_candidate_count": 2,
                "post_action_quality_filtered_count": 0,
                "post_action_type_filtered_count": 0,
                "post_action_type_range_filtered_count": 2,
                "preserve_rule_matched_count": 0,
            },
            summarize_post_action_filtering(
                [
                    (1, keep_drive, "normal"),
                    (2, excluded_drive, "normal"),
                    (3, keep_tape, "normal"),
                    (4, excluded_tape, "normal"),
                ],
                config,
            ),
        )

    def test_post_action_scores_only_usable_roles_when_context_is_strict(self):
        from src.domain.post_actions import (
            default_post_action_config,
            target_state_for_item,
        )
        from src.models.equipment import Drive

        class FakeScoring:
            roles_db = {"usable": {}, "unusable": {}}

            def get_grade_tag(self, score, _area):
                return "SSS" if float(score) >= 2.0 else "D"

        item = Drive(
            uid="strict-drive",
            quality="Gold",
            area=3,
            shape_id="S1",
            main_stats={"m1": 1.0, "m2": 1.0},
            sub_stats={"Good": 1.0},
            role_scores={"usable": 1.0, "unusable": 2.0},
        )
        config = default_post_action_config()
        config["lock"]["enabled"] = True
        config["lock"]["grade"] = "SSS"
        context = EquipmentMatchContext(matches=(RoleSuitMatch(
            "usable", 1, "suit", "套装", True, frozenset({"S1"}), frozenset()),))

        self.assertEqual("normal", target_state_for_item(item, "normal", config, FakeScoring(), score_context=context))

    def test_custom_keep_rules_override_score_based_discard_and_can_lock(self):
        from src.domain.post_actions import default_post_action_config, target_state_for_item
        from src.models.equipment import Drive, Tape

        class FakeScoring:
            roles_db = {"A": {}}

            def get_grade_tag(self, _score, _area):
                return "D"

        config = default_post_action_config()
        config["discard"].update({"enabled": True, "grade": "S"})
        config["custom_keep"] = {
            "tape_rules": [
                {
                    "main_stats": ["攻击力%"],
                    "sub_stats": ["暴击率%", "暴击伤害%"],
                    "minimum_sub_matches": 2,
                    "action": "keep",
                }
            ],
            "drive_rules": [
                {
                    "sub_stats": ["攻击力%", "暴击率%"],
                    "minimum_sub_matches": 2,
                    "action": "lock",
                }
            ],
        }
        tape = Tape(
            uid="future-tape",
            quality="Purple",
            area=15,
            set_name="Set",
            main_stats="攻击力%",
            sub_stats={"暴击率%": 1.0, "暴击伤害%": 2.0},
        )
        drive = Drive(
            uid="future-drive",
            quality="Purple",
            area=3,
            shape_id="H_3",
            main_stats={"攻击力": 1.0, "生命值": 1.0},
            sub_stats={"攻击力%": 1.0, "暴击率%": 1.0},
        )

        self.assertEqual("normal", target_state_for_item(tape, "discarded", config, FakeScoring()))
        self.assertEqual("locked", target_state_for_item(drive, "normal", config, FakeScoring()))

    def test_parser_consumes_first_capture_before_scan_finishes(self):
        from src.services.scanning.pipeline import run_streaming_scan_parse

        events = []

        class FakeScanner:
            def __init__(self, root):
                self.output_dir = str(root)
                self.temp_dir = root / "temp"
                self.temp_dir.mkdir()
                self.committed = False

            def start_scan(self, total_drives, on_capture=None, commit_on_complete=True):
                self.commit_on_complete = commit_on_complete
                for index in range(1, total_drives + 1):
                    path = self.temp_dir / f"raw_drive_{index:04d}.png"
                    path.write_bytes(b"png")
                    events.append(f"capture:{index}")
                    on_capture(str(path), index, total_drives)
                    if index == 1:
                        deadline = time.time() + 1.0
                        while "parse:raw_drive_0001.png" not in events and time.time() < deadline:
                            time.sleep(0.001)
                    events.append(f"scan_after_callback:{index}")
                events.append("scan_done")
                return total_drives

            def _commit_temp_output(self):
                self.committed = True
                events.append("commit")

        class FakeProcessor:
            def __init__(self):
                self.inventory = []
                self.exported = False

            def process_image_file(self, image_path, filename, **_kwargs):
                events.append(f"parse:{filename}")
                self.inventory.append({"filename": filename})
                return SimpleNamespace(item_type="drive"), True

            def _export_to_json(self):
                self.exported = True
                events.append("export")

        with tempfile.TemporaryDirectory() as tmp:
            scanner = FakeScanner(Path(tmp))
            processor = FakeProcessor()

            stats = run_streaming_scan_parse(
                scanner,
                processor,
                total_drives=2,
                parse_during_scan=True,
            state_sync_factory=_fake_state_sync_factory)

        self.assertLess(events.index("parse:raw_drive_0001.png"), events.index("scan_done"))
        self.assertEqual(False, scanner.commit_on_complete)
        self.assertFalse(scanner.committed)  # 解析阶段不发布，背包授权后才改变正式目录。
        self.assertFalse(processor.exported)
        self.assertEqual(2, stats["success_count"])
        self.assertEqual(0, stats["failed_count"])
        self.assertEqual("full", stats["parse_scope"])
        self.assertGreaterEqual(stats["capture_ms"], 0.0)
        self.assertGreaterEqual(stats["parse_ms"], 0.0)
        self.assertGreaterEqual(stats["end_to_end_ms"], stats["capture_ms"])
        self.assertGreaterEqual(stats["parse_tail_ms"], 0.0)
        self.assertGreaterEqual(stats["producer_block_ms"], 0.0)
        self.assertGreaterEqual(stats["queue_high_watermark"], 1)

    def test_default_low_load_parse_waits_until_scan_finishes(self):
        from src.services.scanning.pipeline import run_streaming_scan_parse

        events = []

        class FakeScanner:
            def __init__(self, root):
                self.output_dir = str(root)
                self.temp_dir = root / "temp"
                self.temp_dir.mkdir()

            def start_scan(self, total_drives, on_capture=None, commit_on_complete=True):
                for index in range(1, total_drives + 1):
                    path = self.temp_dir / f"raw_drive_{index:04d}.png"
                    path.write_bytes(b"png")
                    events.append(f"capture:{index}")
                    on_capture(str(path), index, total_drives)
                events.append("scan_done")
                return total_drives

            def _commit_temp_output(self):
                events.append("commit")

        class FakeProcessor:
            def __init__(self):
                self.inventory = []

            def process_image_file(self, image_path, filename, **_kwargs):
                events.append(f"parse:{filename}")
                self.inventory.append({"filename": filename})
                return SimpleNamespace(item_type="drive"), True

            def _export_to_json(self):
                events.append("export")

        with tempfile.TemporaryDirectory() as tmp:
            stats = run_streaming_scan_parse(FakeScanner(Path(tmp)), FakeProcessor(), total_drives=2, state_sync_factory=_fake_state_sync_factory)

        self.assertLess(events.index("scan_done"), events.index("parse:raw_drive_0001.png"))
        self.assertEqual(2, stats["success_count"])
        self.assertEqual(0, stats["failed_count"])

    def test_mouse_pipeline_defers_file_commit_and_runs_shared_state_plan(self):
        from src.services.scanning.pipeline import run_streaming_scan_parse

        events = []

        class FakeScanner:
            output_dir = "screenshots"

            def start_scan(self, total_drives, on_capture=None, commit_on_complete=True):
                on_capture("screenshots/temp/raw_drive_0001.png", 1, total_drives)
                return total_drives

            def _commit_temp_output(self):
                events.append("commit")

            def sync_equipment_states(self, total_drives, state_changes, action_mode="default"):
                events.append(("sync", total_drives, state_changes, action_mode))
                return summarize_state_changes(state_changes)

        class FakeProcessor:
            inventory = []

            def process_image_file(self, _path, _filename, **_kwargs):
                return SimpleNamespace(item_type="drive"), True

        evaluation = SimpleNamespace(
            matching_identity="",
            config={"server_region": "cn", "discard": {"enabled": True}, "lock": {"enabled": False}},
            state_changes=[{"index": 1, "current_state": "normal", "target_state": "locked"}],
            filter_summary={"post_action_candidate_count": 1},
        )
        with patch(
            "src.services.scanning.pipeline.PostActionEvaluator.evaluate",
            return_value=evaluation,
        ):
            stats = run_streaming_scan_parse(
                FakeScanner(),
                FakeProcessor(),
                total_drives=1,
                parse_during_scan=True,
                post_actions_config=evaluation.config,
                allow_post_actions=True,
            state_sync_factory=_fake_state_sync_factory)

        self.assertNotIn("commit", events)
        self.assertEqual("sync", events[0][0])
        self.assertEqual(("sync", 1, evaluation.state_changes, "default"), events[0])
        self.assertTrue(stats["post_actions_enabled"])
        self.assertEqual(1, stats["lock_set_count"])

    def test_partial_state_management_returns_full_parse_results_and_issues(self):
        from src.services.scanning.pipeline import run_streaming_scan_parse

        events = []

        class FakeScanner:
            output_dir = "screenshots"

            def start_scan(self, total_drives, on_capture=None, commit_on_complete=True):
                for index in (1, 2):
                    on_capture(f"screenshots/temp/raw_drive_{index:04d}.png", index, total_drives)
                return total_drives

            def _commit_temp_output(self):
                events.append("commit")

            def sync_equipment_states(self, *_args, **_kwargs):
                events.append("sync")
                return {
                    "post_action_applied_count": 1,
                    "post_action_issue_count": 1,
                    "post_action_issues": [{"index": 2, "target_state": "locked", "reason": "identity_mismatch"}],
                }

        class FakeProcessor:
            def __init__(self):
                self.inventory = []

            def process_image_file(self, _path, filename, **_kwargs):
                item = SimpleNamespace(item_type="drive", filename=filename)
                self.inventory.append(item)
                return item, True

        evaluation = SimpleNamespace(
            matching_identity="",
            config={"discard": {"enabled": True}},
            state_changes=[
                {"index": index, "current_state": "normal", "target_state": "locked"}
                for index in (2, 1)
            ],
            filter_summary={"post_action_candidate_count": 2},
        )
        processor = FakeProcessor()
        with patch("src.services.scanning.pipeline.PostActionEvaluator.evaluate", return_value=evaluation), \
                patch("src.services.scanning.pipeline.equipment_screenshot_state", return_value="normal"), \
                patch("src.services.scanning.pipeline.time.sleep"):
            stats = run_streaming_scan_parse(
                FakeScanner(), processor, 2, parse_during_scan=True, post_actions_config=evaluation.config,
            state_sync_factory=_fake_state_sync_factory)

        self.assertEqual(["sync"], events)
        self.assertEqual(2, stats["total_count"])
        self.assertEqual(2, stats["success_count"])
        self.assertEqual("full", stats["parse_scope"])
        self.assertEqual(2, len(processor.inventory))
        self.assertEqual(1, stats["post_action_applied_count"])
        self.assertEqual(1, stats["post_action_issue_count"])
        self.assertEqual(2, stats["post_action_issues"][0]["index"])

    def test_stale_generation_discards_pipeline_before_file_commit(self):
        from src.services.scanning.pipeline import run_streaming_scan_parse

        class FakeScanner:
            output_dir = "screenshots"
            committed = False

            def start_scan(self, total_drives, on_capture=None, commit_on_complete=True):
                on_capture("screenshots/temp/raw_drive_0001.png", 1, total_drives)
                return total_drives

            def _commit_temp_output(self):
                self.committed = True

        class FakeProcessor:
            inventory = []

            def process_image_file(self, _path, _filename, **_kwargs):
                return SimpleNamespace(item_type="drive"), True

        scanner = FakeScanner()
        stats = run_streaming_scan_parse(
            scanner,
            FakeProcessor(),
            total_drives=1,
            parse_during_scan=True,
            allow_post_actions=False,
            result_is_current=lambda: False,
        state_sync_factory=_fake_state_sync_factory)

        self.assertTrue(stats["discarded_stale"])
        self.assertFalse(scanner.committed)

    def test_amd_low_load_forces_post_scan_parse_and_throttles(self):
        from src.services.scanning import pipeline as streaming_scan_service

        events = []
        sleeps = []
        original_sleep = streaming_scan_service.time.sleep

        class FakeScanner:
            def __init__(self, root):
                self.output_dir = str(root)
                self.temp_dir = root / "temp"
                self.temp_dir.mkdir()

            def start_scan(self, total_drives, on_capture=None, commit_on_complete=True):
                for index in range(1, total_drives + 1):
                    path = self.temp_dir / f"raw_drive_{index:04d}.png"
                    path.write_bytes(b"png")
                    events.append(f"capture:{index}")
                    on_capture(str(path), index, total_drives)
                events.append("scan_done")
                return total_drives

            def _commit_temp_output(self):
                events.append("commit")

        class FakeProcessor:
            def __init__(self):
                self.inventory = []

            def process_image_file(self, image_path, filename, **_kwargs):
                events.append(f"parse:{filename}")
                self.inventory.append({"filename": filename})
                return SimpleNamespace(item_type="drive"), True

            def _export_to_json(self):
                events.append("export")

        streaming_scan_service.time.sleep = lambda seconds: sleeps.append(seconds)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                stats = streaming_scan_service.run_streaming_scan_parse(
                    FakeScanner(Path(tmp)),
                    FakeProcessor(),
                    total_drives=2,
                    parse_during_scan=True,
                    low_load_mode=True,
                state_sync_factory=_fake_state_sync_factory)
        finally:
            streaming_scan_service.time.sleep = original_sleep

        self.assertLess(events.index("scan_done"), events.index("parse:raw_drive_0001.png"))
        self.assertEqual([0.12, 0.12], sleeps)
        self.assertEqual(2, stats["success_count"])
        self.assertEqual(0, stats["failed_count"])


@pytest.mark.parametrize("problem", ("failed", "pending", "count", "duplicate"))
def test_incomplete_parse_never_evaluates_or_dispatches(problem, tmp_path):
    from src.services.scanning.pipeline import ScanParseResult, run_streaming_scan_parse

    retained = tmp_path / "existing.png"
    retained.write_bytes(b"existing-image")
    database = tmp_path / "inventory.sqlite3"
    database.write_bytes(b"existing-inventory")
    result = ScanParseResult(captured_count=2, added_paths=["a", "b"],
                            parsed_items=[(1, object(), "normal"), (2, object(), "normal")])
    if problem == "failed":
        result.failed_paths.append("failed.png")
    elif problem == "pending":
        result.pending_manual_items.append({"image_path": "pending.png"})
    elif problem == "duplicate":
        result.duplicate_paths.append("duplicate.png")
    result.parsed_items.pop()
    scanner, factory, ready = Mock(), Mock(), Mock()
    with patch("src.services.scanning.pipeline.ScanParseCoordinator.run", return_value=result), \
            patch("src.services.scanning.pipeline.PostActionEvaluator") as evaluator:
        stats = run_streaming_scan_parse(scanner, SimpleNamespace(inventory=[]), 2,
            post_actions_config={"discard": {"enabled": True}}, state_sync_factory=factory,
            post_action_ready_callback=ready)
    evaluator.assert_not_called()
    factory.assert_not_called()
    ready.assert_not_called()
    scanner._commit_temp_output.assert_not_called()
    assert stats["total_count"] == 2
    assert stats["post_actions_enabled"] is True
    assert retained.read_bytes() == b"existing-image"
    assert database.read_bytes() == b"existing-inventory"
    assert sorted(row.name for row in tmp_path.iterdir()) == ["existing.png", "inventory.sqlite3"]


@pytest.mark.parametrize("invalidated", ("cancel", "generation"))
def test_evaluation_rechecks_frozen_guard_before_factory_and_ready(invalidated):
    from src.services.scanning.pipeline import ScanParseResult, run_streaming_scan_parse

    guard = {"cancel": False, "current": True}
    config = {"discard": {"enabled": True}}
    evaluation = SimpleNamespace(matching_identity="", config=config, state_changes=[{"index": 1}], filter_summary={})
    def evaluate(*_):
        guard["cancel" if invalidated == "cancel" else "current"] = invalidated == "cancel"
        return evaluation
    result = ScanParseResult(captured_count=1, parsed_items=[(1, object(), "normal")])
    factory, ready = Mock(), Mock()
    with patch("src.services.scanning.pipeline.ScanParseCoordinator.run", return_value=result), \
            patch("src.services.scanning.pipeline.PostActionEvaluator.evaluate", side_effect=evaluate) as evaluator:
        stats = run_streaming_scan_parse(Mock(), SimpleNamespace(inventory=[]), 1,
            post_actions_config=config, state_sync_factory=factory, post_action_ready_callback=ready,
            cancel_check=lambda: guard["cancel"], result_is_current=lambda: guard["current"])
    evaluator.assert_called_once()
    factory.assert_not_called()
    ready.assert_not_called()
    assert stats.get("discarded_stale", False) is (invalidated == "generation")
    if invalidated == "cancel":
        assert stats["post_actions_enabled"] is True
        assert stats["lock_set_count"] == 0


if __name__ == "__main__":
    unittest.main()
