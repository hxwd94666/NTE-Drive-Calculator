# 验证应用专用战报容器的公共格式契约。

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.integrations.battle_report_bundle import (
    BATTLE_REPORT_BUNDLE_MAGIC,
    BattleReportBundleError,
    decode_battle_report_bundle,
    encode_battle_report_bundle,
    read_battle_report_bundle,
    write_battle_report_bundle_atomic,
)


class BattleReportBundleTests(unittest.TestCase):
    def test_round_trip_uses_binary_header_compression_and_utf8_json(self) -> None:
        payload = {"format": {"version": 1}, "reports": [{"name": "测试战报"}]}

        encoded = encode_battle_report_bundle(payload)
        decoded = decode_battle_report_bundle(encoded)

        self.assertTrue(encoded.startswith(BATTLE_REPORT_BUNDLE_MAGIC))
        self.assertNotIn("测试战报".encode("utf-8"), encoded)
        self.assertEqual(payload, decoded)

    def test_tampered_package_is_rejected(self) -> None:
        encoded = bytearray(encode_battle_report_bundle({"reports": [1]}))
        encoded[-1] ^= 0x01

        with self.assertRaises(BattleReportBundleError):
            decode_battle_report_bundle(bytes(encoded))

    def test_failed_context_recheck_leaves_existing_target_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "report.ntebr"
            target.write_bytes(b"existing")

            with self.assertRaisesRegex(RuntimeError, "stale"):
                write_battle_report_bundle_atomic(
                    target,
                    {"reports": [1]},
                    before_replace=lambda: (_ for _ in ()).throw(RuntimeError("stale")),
                )

            self.assertEqual(b"existing", target.read_bytes())
            self.assertEqual([], list(target.parent.glob(".*.tmp")))

    def test_atomic_write_can_be_read_by_the_application_codec(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "report.ntebr"
            write_battle_report_bundle_atomic(
                target,
                {"reports": [{"id": 7}]},
                before_replace=lambda: None,
            )

            self.assertEqual({"reports": [{"id": 7}]}, read_battle_report_bundle(target))


if __name__ == "__main__":
    unittest.main()
