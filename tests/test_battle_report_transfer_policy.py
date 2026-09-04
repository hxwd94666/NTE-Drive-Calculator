# 验证 v2 战报导出冻结正确的逐角色装备证据。
"""Verify v2 export freezes the correct per-character equipment evidence."""

from __future__ import annotations

import unittest

from src.services.battle_report_transfer_service import (
    BATTLE_REPORT_TRANSFER_FORMAT,
    BATTLE_REPORT_TRANSFER_VERSION,
    SUPPORTED_SOURCE_USER_DATABASE_SCHEMAS,
    BattleReportTransferService,
)
from src.storage.sqlite.user_data_support import (
    SCHEMA_VERSION,
    UserDataValidationError,
)


def _bundle_payload(source_schema: object) -> dict:
    return {
        "format": {
            "name": BATTLE_REPORT_TRANSFER_FORMAT,
            "version": BATTLE_REPORT_TRANSFER_VERSION,
        },
        "bundle": {
            "bundle_id": "bundle-fixture",
            "source_account_nickname": "测试账号",
            "user_database_schema_version": source_schema,
            "static_data": {},
        },
        "manifest": {"report_count": 1},
        "reports": [{
            "source_account_nickname": "来源账号",
            "export_account_nickname": "导出账号",
            "database_rows": {},
            "locked_equipment_at_export": [],
        }],
    }


class BattleReportTransferPolicyTests(unittest.TestCase):
    def test_import_accepts_only_audited_user_schema_36_through_39(self) -> None:
        self.assertEqual(
            frozenset({36, 37, 38, 39}),
            SUPPORTED_SOURCE_USER_DATABASE_SCHEMAS,
        )
        self.assertIn(SCHEMA_VERSION, SUPPORTED_SOURCE_USER_DATABASE_SCHEMAS)
        for schema in (36, 37, 38, 39):
            reports = BattleReportTransferService._validate_bundle(
                _bundle_payload(schema)
            )
            self.assertEqual(1, len(reports))
        for schema in (35, 40, "36", None, [], True):
            with self.subTest(schema=schema), self.assertRaises(
                UserDataValidationError
            ):
                BattleReportTransferService._validate_bundle(
                    _bundle_payload(schema)
                )

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
