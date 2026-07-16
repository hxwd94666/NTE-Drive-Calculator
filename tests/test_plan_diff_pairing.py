# 测试配装方案差异中的驱动配对逻辑。
import unittest

from src.features.allocation.plan_diff_pairing import (
    drive_item_area,
    drive_shape_family,
    pair_drive_diff_items,
    pair_diff_items_by_key,
)


class PlanDiffPairingTests(unittest.TestCase):
    def test_drive_shape_family_groups_rotations(self):
        self.assertEqual("I_3", drive_shape_family("H_3"))
        self.assertEqual("L_3", drive_shape_family("L_3_TR"))

    def test_drive_item_area_prefers_explicit_area(self):
        item = {"shape_id": "V_3", "area": 3}
        self.assertEqual(3, drive_item_area(item, {"L_3_TR": 3}))

    def test_pair_diff_items_by_key_pairs_within_same_bucket(self):
        old_items = [{"uid": "a", "shape_id": "H_3"}, {"uid": "b", "shape_id": "H_3"}]
        new_items = [{"uid": "c", "shape_id": "H_3"}]
        pairs, unmatched_old, unmatched_new = pair_diff_items_by_key(
            old_items,
            new_items,
            lambda item: item["shape_id"],
        )
        self.assertEqual(1, len(pairs))
        self.assertEqual("a", pairs[0][0]["uid"])
        self.assertEqual("c", pairs[0][1]["uid"])
        self.assertEqual(["b"], [item["uid"] for item in unmatched_old])
        self.assertEqual([], unmatched_new)

    def test_pair_drive_diff_items_matches_by_area_when_families_differ(self):
        removed = [{"uid": "old", "shape_id": "L_3_TR", "area": 3}]
        added = [{"uid": "new", "shape_id": "V_3", "area": 3}]
        pairs, unmatched_old, unmatched_new = pair_drive_diff_items(removed, added, {"L_3_TR": 3, "V_3": 3})
        self.assertEqual([("old", "new")], [(left["uid"], right["uid"]) for left, right in pairs])
        self.assertEqual([], unmatched_old)
        self.assertEqual([], unmatched_new)
