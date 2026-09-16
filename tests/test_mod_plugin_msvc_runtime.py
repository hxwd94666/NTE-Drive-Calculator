# 验证采集组件缺失运行库时的预检与明确提示。
from pathlib import Path
from unittest.mock import patch

import pytest

from src.services.mod_plugin_loading_service import (
    ModPluginLoadingError,
    ModPluginLoadingService,
    probe_mod_plugin_msvc_runtime,
)


def test_missing_atomic_wait_runtime_blocks_preflight_then_allows_retry():
    missing = {"MSVCP140_ATOMIC_WAIT.dll"}

    def available(path):
        return path.name not in missing

    with patch("src.services.mod_plugin_loading_service.os.name", "nt"), patch.object(
        Path, "is_file", available
    ):
        status = probe_mod_plugin_msvc_runtime()
        assert not status["ready"]
        assert status["files"]["MSVCP140_ATOMIC_WAIT.dll"] is False
        with pytest.raises(ModPluginLoadingError, match="MSVCP140_ATOMIC_WAIT"):
            ModPluginLoadingService._require_msvc_runtime()
        missing.clear()
        assert probe_mod_plugin_msvc_runtime()["ready"]
        ModPluginLoadingService._require_msvc_runtime()
