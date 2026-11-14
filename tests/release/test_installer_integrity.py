# 验证安装器目录保持普通完整性、成品支持 High 启动，并保留失败回退。
import hashlib
from pathlib import Path
import sys

import pytest

from tools.release import installer_integrity as integrity

NTE_TEST_TIER = "core"


class Labels:
    def __init__(self, current="S:(ML;;NW;;;LW)", process=8192):
        self.current, self.process, self.writes = current, process, []

    def process_rid(self):
        return self.process

    def read(self, path):
        return self.current

    def write(self, path, value):
        self.writes.append((path, value))
        self.current = value


@pytest.mark.parametrize("sddl,rid", [("", 8192), ("S:(ML;ID;NW;;;LW)", 4096),
                                     ("S:(ML;OICI;NW;;;ME)", 8192),
                                     ("S:(ML;;NW;;;S-1-16-12288)", 12288)])
def test_label_semantics(sddl, rid):
    assert integrity.label_rid(sddl) == rid


def test_unknown_label_fails_closed():
    with pytest.raises(ValueError):
        integrity.label_rid("S:(ML;;NW;;;unexpected)")


def test_low_builder_does_not_change_or_create_output(tmp_path, monkeypatch):
    api = Labels(process=4096)
    monkeypatch.setattr(integrity.sys, "platform", "win32")
    monkeypatch.setattr(integrity, "_api", lambda: api)
    output = tmp_path / "output"
    with pytest.raises(RuntimeError, match="构建解释器处于 Low"):
        integrity.prepare_installer_output(output)
    assert not output.exists()
    assert not api.writes


def test_output_keeps_path_and_limits_changes_to_owned_directory(tmp_path, monkeypatch):
    api = Labels()
    monkeypatch.setattr(integrity.sys, "platform", "win32")
    monkeypatch.setattr(integrity, "_api", lambda: api)
    output = tmp_path / "output"
    assert integrity.prepare_installer_output(output)
    assert api.writes == [(output, "S:(ML;OICI;NW;;;ME)")]
    assert output.is_dir()


@pytest.mark.parametrize("sddl", ["S:(ML;;NW;;;HI)", "S:(ML;;NW;;;SI)"])
def test_high_or_higher_artifact_is_not_relabelled(tmp_path, monkeypatch, sddl):
    api = Labels(current=sddl)
    monkeypatch.setattr(integrity.sys, "platform", "win32")
    monkeypatch.setattr(integrity, "_api", lambda: api)
    setup = tmp_path / "setup.exe"
    setup.write_bytes(b"fixture")
    assert not integrity.finalize_installer_artifact(setup)
    assert not api.writes


@pytest.mark.parametrize("sddl", ["", "S:(ML;;NW;;;LW)", "S:(ML;ID;NW;;;ME)"])
def test_installer_artifact_must_not_cap_administrator_process_at_medium(tmp_path, monkeypatch, sddl):
    api = Labels(current=sddl)
    monkeypatch.setattr(integrity.sys, "platform", "win32")
    monkeypatch.setattr(integrity, "_api", lambda: api)
    setup = tmp_path / "setup.exe"
    setup.write_bytes(b"fixture")
    assert integrity.finalize_installer_artifact(setup)
    assert api.writes == [(setup, "S:(ML;;NW;;;HI)")]
    assert setup.read_bytes() == b"fixture"


def test_failed_readback_restores_only_original_label(tmp_path, monkeypatch):
    api = Labels()
    original = api.current
    monkeypatch.setattr(integrity.sys, "platform", "win32")
    monkeypatch.setattr(integrity, "_api", lambda: api)

    def write(path, value):
        api.writes.append((path, value))
        api.current = "S:(ML;;NW;;;ME)" if len(api.writes) == 1 else value

    monkeypatch.setattr(api, "write", write)
    setup = tmp_path / "setup.exe"
    setup.write_bytes(b"fixture")
    with pytest.raises(RuntimeError, match="回读不一致"):
        integrity.finalize_installer_artifact(setup)
    assert api.current == original
    assert api.writes[-1] == (setup, original)
    assert setup.read_bytes() == b"fixture"


def test_builder_does_not_report_complete_after_final_check_failure(tmp_path, monkeypatch, capsys):
    import build_installer as builder

    setup = tmp_path / "NTE_Drive_Calc_Setup_1.0.0.exe"
    setup.write_bytes(b"fixture")
    monkeypatch.setattr(sys, "argv", ["build_installer.py", "--version", "1.0.0", "--skip-app-build"])
    monkeypatch.setattr(builder, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(builder, "_configure_output_root", lambda *_: None)
    monkeypatch.setattr(builder, "prepare_installer_output", lambda *_: False)
    monkeypatch.setattr(builder, "_ensure_app_bundle", lambda **_: None)
    monkeypatch.setattr(builder, "prepare_installer_asset_stage", lambda *_: [])
    monkeypatch.setattr(builder, "validate_packaged_component_bundle", lambda *_, **__: None)
    monkeypatch.setattr(builder, "validate_packaged_ocr_models", lambda *_: None)
    monkeypatch.setattr(builder, "_find_vigem_installer", lambda *_: (Path("fixture.exe"), True))
    monkeypatch.setattr(builder, "_write_iss", lambda **_: None)
    monkeypatch.setattr(builder, "_find_iscc", lambda *_: Path("ISCC.exe"))
    monkeypatch.setattr(builder, "_run", lambda *_: None)

    def reject(_):
        raise RuntimeError("fixture integrity verification failed")

    monkeypatch.setattr(builder, "finalize_installer_artifact", reject)
    assert builder.main() == 1
    output = capsys.readouterr().out
    assert "fixture integrity verification failed" in output
    assert "Installer complete:" not in output


@pytest.mark.skipif(sys.platform != "win32", reason="Windows mandatory label API")
def test_windows_same_directory_new_and_existing_artifact_keeps_bytes(tmp_path):
    api = integrity.WindowsLabels()
    if api.process_rid() < integrity.MEDIUM:
        pytest.fail("安装器专项要求普通完整性的解释器；不跳过受影响构建环境。")
    parent = tmp_path / "installer"
    parent.mkdir()
    parent_before = api.read(parent)
    output = parent / "output"
    output.mkdir()
    api.write(output, "S:(ML;OICI;NW;;;LW)")
    setup = output / "setup.exe"
    setup.write_bytes(b"isolated installer bytes")
    digest = hashlib.sha256(setup.read_bytes()).hexdigest()
    assert integrity.label_rid(api.read(setup)) < integrity.MEDIUM
    assert integrity.prepare_installer_output(output)
    integrity.finalize_installer_artifact(setup)
    assert integrity.label_rid(api.read(setup)) == integrity.HIGH
    next_setup = output / "next.exe"
    next_setup.write_bytes(b"next build")
    assert integrity.label_rid(api.read(next_setup)) == integrity.MEDIUM
    assert integrity.finalize_installer_artifact(next_setup)
    assert integrity.label_rid(api.read(next_setup)) == integrity.HIGH
    assert hashlib.sha256(setup.read_bytes()).hexdigest() == digest
    assert api.read(parent) == parent_before
