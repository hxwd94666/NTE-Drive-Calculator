# 覆盖旧组件升级证据与首次安装不误报的公共行为。
from __future__ import annotations

from types import SimpleNamespace

from src.services.component_upgrade_guide import inspect_upgrade_evidence


def _game(tmp_path):
    executable = tmp_path / "HTGame.exe"
    executable.write_bytes(b"fixture")
    return executable


def test_fresh_install_has_no_upgrade_prompt(tmp_path):
    executable = _game(tmp_path)
    result = inspect_upgrade_evidence(
        application_root=tmp_path, game_path=str(executable), deployment={},
    )
    assert result.kind == "none"
    assert not result.requires_attention


def test_legacy_proxy_is_upgrade_evidence(tmp_path, monkeypatch):
    executable = _game(tmp_path)
    (tmp_path / "dwmapi.dll").write_bytes(b"old")
    monkeypatch.setattr(
        "src.services.component_upgrade_guide.mod_workspace_registry_snapshot",
        lambda: (False, None),
    )
    result = inspect_upgrade_evidence(
        application_root=tmp_path, game_path=str(executable), deployment={},
    )
    assert result.kind == "legacy"
    assert result.requires_attention


def test_mismatched_current_component_needs_update(tmp_path, monkeypatch):
    executable = _game(tmp_path)
    monkeypatch.setattr(
        "src.services.component_upgrade_guide.inspect_deployed_native_plugin",
        lambda **_kwargs: SimpleNamespace(files_compatible=False),
    )
    result = inspect_upgrade_evidence(
        application_root=tmp_path, game_path=str(executable),
        deployment={"deployment_layout": "native-capture-v1", "managed_files": {"d3d12.dll": "sha"}},
    )
    assert result.kind == "update"


def test_matching_component_requires_business_verification(tmp_path, monkeypatch):
    executable = _game(tmp_path)
    monkeypatch.setattr(
        "src.services.component_upgrade_guide.inspect_deployed_native_plugin",
        lambda **_kwargs: SimpleNamespace(files_compatible=True),
    )
    result = inspect_upgrade_evidence(
        application_root=tmp_path, game_path=str(executable),
        deployment={"deployment_layout": "native-capture-v1", "managed_files": {"d3d12.dll": "sha"}},
    )
    assert result.kind == "ready"
    assert "连接" in result.detail
