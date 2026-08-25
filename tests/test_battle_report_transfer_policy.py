"""Verify v2 export freezes the correct per-character equipment evidence."""

from __future__ import annotations

import unittest

from src.services.battle_report_transfer_service import (
    BATTLE_REPORT_TRANSFER_VERSION,
    BattleReportTransferService,
)


class BattleReportTransferPolicyTests(unittest.TestCase):
    def test_inactive_saved_equipment_override_still_wins_export(self) -> None:
        frozen_item = {"kind": "core", "item_id": "frozen"}
        calibrated_item = {"kind": "core", "item_id": "calibrated"}

        rows = BattleReportTransferService._locked_equipment_at_export(
            frozen_build={
                "characters": [
                    {"character_id": 1004, "equipment": [frozen_item]}
                ]
            },
            build_edit={
                "is_active": False,
                "characters": [
                    {
                        "character_id": 1004,
                        "profile": {
                            "equipment_override": [calibrated_item],
                        },
                    }
                ],
            },
            import_locks={},
        )

        self.assertEqual(2, BATTLE_REPORT_TRANSFER_VERSION)
        self.assertEqual("calibrated_build_edit", rows[0][
            "equipment_source_kind"
        ])
        self.assertEqual([calibrated_item], rows[0]["equipment"])

    def test_imported_equipment_lock_wins_reexport(self) -> None:
        imported_item = {"kind": "module", "item_id": "source-evidence"}

        rows = BattleReportTransferService._locked_equipment_at_export(
            frozen_build={
                "characters": [
                    {"character_id": 1004, "equipment": [{"item_id": "local"}]}
                ]
            },
            build_edit=None,
            import_locks={
                1004: {
                    "equipment_source_kind": "calibrated_build_edit",
                    "equipment": [imported_item],
                }
            },
        )

        self.assertEqual("imported_locked", rows[0]["equipment_source_kind"])
        self.assertEqual(
            "calibrated_build_edit",
            rows[0]["original_equipment_source_kind"],
        )
        self.assertEqual([imported_item], rows[0]["equipment"])


if __name__ == "__main__":
    unittest.main()
