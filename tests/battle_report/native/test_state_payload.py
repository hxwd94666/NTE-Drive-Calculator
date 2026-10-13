# 验证状态浅投影保留冻结值和证据引用，同时排除内核不消费的展示字段。
import json
import unittest

from src.services.battle_report.native.state_payload import (
    fadia_character, finalize_rows,
)
from tests.battle_report.buffs.test_attribute_projection import _interval


class StatePayloadTests(unittest.TestCase):

    def test_finalize_sends_only_required_modifier_tags_and_preserves_tuple(self):
        interval = _interval("sample", start_us=0)
        wire, = finalize_rows((interval,))
        self.assertEqual({"start_us", "end_us", "source_character_id", "buff_asset_path", "modifiers"}, set(wire))
        self.assertEqual({"target_require_tags"}, set(wire["modifiers"][0]))
        self.assertIs(interval.modifiers[0].target_require_tags, wire["modifiers"][0]["target_require_tags"])

    def test_fadia_keeps_missing_null_and_falsy_values_without_materializing_equipment(self):
        character = {
            "awakening_level": "0", "equipment": object(),
            "profile": {"awakening_level": 0, "selected_awaken_effect_ids": (), "unused": object()},
            "stats": [{"source_group": "resolved", "property_id": "PanelHP", "value": None, "unused": object()}],
        }
        actual = fadia_character(character)
        self.assertEqual("0", actual["awakening_level"])
        self.assertEqual({"awakening_level": 0, "selected_awaken_effect_ids": ()}, actual["profile"])
        self.assertIsNone(actual["stats"][0]["value"])
        self.assertNotIn("equipment", actual)
        json.dumps(actual)
        self.assertEqual({}, fadia_character({}))
        self.assertEqual({"profile": None, "stats": None}, fadia_character({"profile": None, "stats": None}))
