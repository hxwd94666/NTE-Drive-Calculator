# 验证新页面共享的官方空幕与驱动加成计算口径。
import unittest

from src.services.allocation_context import AllocationCandidate, OfficialStat
from src.services.official_equipment_bonus_service import calculate_official_equipment_stats


def _candidate(
    *,
    uid: tuple[int, int],
    kind: str,
    grid_count: int | None,
    quality: str = "orange",
    main_stats: tuple[OfficialStat, ...],
    sub_stats: tuple[OfficialStat, ...],
) -> AllocationCandidate:
    return AllocationCandidate(
        uid_slot=uid[0],
        uid_serial=uid[1],
        kind=kind,
        item_id=f"{kind}-{uid[0]}-{uid[1]}",
        suit_id=None,
        geometry=f"Shu{grid_count}" if grid_count else None,
        grid_count=grid_count,
        quality=quality,
        level=0,
        max_level=20,
        locked=False,
        discarded=False,
        equipped=False,
        equipped_character_id=None,
        is_duplicate_drive=False,
        duplicate_group_id=None,
        duplicate_index=None,
        duplicate_count=None,
        main_stats=main_stats,
        sub_stats=sub_stats,
    )


class OfficialEquipmentBonusServiceTests(unittest.TestCase):
    def test_reproduces_old_core_module_and_extra_shape_rules(self) -> None:
        core = _candidate(
            uid=(1, 1),
            kind="core",
            grid_count=None,
            main_stats=(OfficialStat("CritBase", 0.30, True),),
            sub_stats=(OfficialStat("AtkUp", 0.10, True),),
        )
        module = _candidate(
            uid=(2, 1),
            kind="module",
            grid_count=2,
            main_stats=(
                OfficialStat("AtkAdd", 8.0, False),
                OfficialStat("HPMaxAdd", 112.0, False),
            ),
            sub_stats=(OfficialStat("CritBase", 0.02, True),),
        )

        totals = {
            row.property_id: row
            for row in calculate_official_equipment_stats(
                (core, module),
                extra_shape_label="Type-2",
                extra_shape_buffs=(("CritBase", 6.0),),
                property_percent={"CritBase": True, "AtkUp": True},
            )
        }

        self.assertAlmostEqual(0.38, totals["CritBase"].value)
        self.assertAlmostEqual(0.10, totals["AtkUp"].value)
        self.assertEqual(42.0, totals["AtkAdd"].value)
        self.assertEqual(560.0, totals["HPMaxAdd"].value)
        self.assertTrue(totals["CritBase"].percent)
        self.assertFalse(totals["AtkAdd"].percent)

    def test_module_intrinsic_stats_follow_quality(self) -> None:
        expected = {
            "orange": (42.0, 560.0),
            "purple": (33.6, 448.0),
            "blue": (25.2, 336.0),
        }
        for index, (quality, values) in enumerate(expected.items(), start=1):
            with self.subTest(quality=quality):
                module = _candidate(
                    uid=(index, 1),
                    kind="module",
                    grid_count=2,
                    quality=quality,
                    main_stats=(OfficialStat("AtkAdd", 9999.0, False),),
                    sub_stats=(OfficialStat("CritBase", 0.02, True),),
                )
                totals = {
                    row.property_id: row.value
                    for row in calculate_official_equipment_stats((module,))
                }
                self.assertEqual(values[0], totals["AtkAdd"])
                self.assertEqual(values[1], totals["HPMaxAdd"])

    def test_module_without_substats_matches_old_empty_drive_filter(self) -> None:
        module = _candidate(
            uid=(2, 1),
            kind="module",
            grid_count=2,
            main_stats=(OfficialStat("AtkAdd", 42.0, False),),
            sub_stats=(),
        )

        self.assertEqual((), calculate_official_equipment_stats((module,)))
