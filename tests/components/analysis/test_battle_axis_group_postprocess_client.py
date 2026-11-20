# 验证 Mixin 剥离、专用战报轴团队后处理客户端工厂以及 UID 脱敏日志契约。
"""Verify NteCoreBattleQueryMixin removal, dedicated axis postprocess client factory, and UID redaction."""

from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from src.features.drive_assembly.assembly_session import _log_assembly_plan_diagnostics
from src.integrations.analysis_core.analysis_core_release import (
    create_battle_axis_group_postprocess_client,
)
from src.integrations.analysis_core.nte_analysis_core import (
    ENGINE_VERSION,
    NativeAnalysisError,
    NteAnalysisCoreClient,
)
from src.integrations.native_core import nte_core_battle_client
from src.integrations.native_core.nte_core import NteCoreClient
from src.observability.redaction import user_id_hash


class ArchitectureAndComplianceTests(unittest.TestCase):
    def test_nte_core_client_strips_battle_query_mixin_completely(self) -> None:
        self.assertFalse(hasattr(nte_core_battle_client, "NteCoreBattleQueryMixin"))
        self.assertEqual(NteCoreClient.__bases__, (object,))

        client = NteCoreClient(command=("fake-core",))
        recorded: list[tuple[str, dict[str, object]]] = []

        def fake_call(method: str, params: dict[str, object] | None = None) -> dict[str, object]:
            recorded.append((method, dict(params or {})))
            return {"ok": True}

        client.call = fake_call  # type: ignore[method-assign]
        self.assertEqual(
            client.get_battle_record(battle_record_id="rec-1", subtract_time_stop=False),
            {"ok": True},
        )
        self.assertEqual(
            client.get_battle_axis(battle_record_id="rec-1", cursor="cur-2", limit=100),
            {"ok": True},
        )
        self.assertEqual(
            recorded,
            [
                ("battle.get_record", {"battle_record_id": "rec-1", "subtract_time_stop": False}),
                ("battle.get_axis", {"battle_record_id": "rec-1", "cursor": "cur-2", "limit": 100}),
            ],
        )
        with self.assertRaises(ValueError):
            client.get_battle_axis(battle_record_id="rec-1", limit=0)

    def test_create_battle_axis_group_postprocess_client_enforces_capability(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            fake_exe = Path(tmpdir) / "nte-analysis-core.exe"
            fake_exe.write_bytes(b"")
            capable = NteAnalysisCoreClient(
                fake_exe,
                dataset_version="ds-1",
                engine_version=ENGINE_VERSION,
                capabilities=frozenset({"battle_axis_group_postprocess_v1", "allocation_v2"}),
            )
            with patch(
                "src.integrations.analysis_core.analysis_core_release.create_bundled_analysis_client",
                return_value=capable,
            ):
                resolved = create_battle_axis_group_postprocess_client(static_database_path=Path("static.sqlite3"))
            self.assertIs(resolved, capable)
            self.assertTrue(resolved.supports_battle_axis_group_postprocess)

            incapable = NteAnalysisCoreClient(
                fake_exe,
                dataset_version="ds-1",
                engine_version=ENGINE_VERSION,
                capabilities=frozenset({"allocation_v2"}),
            )
            with patch(
                "src.integrations.analysis_core.analysis_core_release.create_bundled_analysis_client",
                return_value=incapable,
            ):
                with self.assertRaisesRegex(NativeAnalysisError, "battle_axis_group_postprocess_v1"):
                    create_battle_axis_group_postprocess_client(static_database_path=Path("static.sqlite3"))

            with patch(
                "src.integrations.analysis_core.analysis_core_release.create_bundled_analysis_client",
                return_value=None,
            ):
                with self.assertRaisesRegex(NativeAnalysisError, "未部署"):
                    create_battle_axis_group_postprocess_client(static_database_path=Path("static.sqlite3"))

    def test_user_id_hash_redacts_deterministically(self) -> None:
        raw_uid = "nte-module-42-987654321"
        digest = user_id_hash(raw_uid)
        self.assertEqual(len(digest), 16)
        self.assertEqual(digest, user_id_hash(f"  {raw_uid}  "))
        self.assertNotIn("987654321", digest)
        self.assertNotIn("nte-module", digest)
        self.assertEqual(user_id_hash(None), "unknown")
        self.assertEqual(user_id_hash(""), "unknown")
        self.assertEqual(user_id_hash("未知"), "unknown")

    def test_assembly_session_diagnostics_log_uses_user_id_hash(self) -> None:
        raw_uid = "nte-module-7-123456789"
        plan = {
            "roles": ["测试角色"],
            "ready_count": 1,
            "role_count": 1,
            "role_plans": [
                {
                    "role_name": "测试角色",
                    "available": True,
                    "tape_count": 0,
                    "drive_count": 1,
                    "actions": [],
                    "drive_blocks": [
                        {
                            "block_id": "b1",
                            "drive": {"uid": raw_uid, "quality": "SSR", "shape_id": "1x1"},
                        }
                    ],
                }
            ],
        }
        messages: list[str] = []
        with patch(
            "src.features.drive_assembly.assembly_session.logger.info",
            side_effect=lambda msg, *args: messages.append(str(msg)),
        ):
            _log_assembly_plan_diagnostics(plan, (1920, 1080), (0, 0, 1920, 1080))
        joined = "\n".join(messages)
        self.assertIn(f"user_id_hash={user_id_hash(raw_uid)}", joined)
        self.assertNotIn(raw_uid, joined)


if __name__ == "__main__":
    unittest.main()
