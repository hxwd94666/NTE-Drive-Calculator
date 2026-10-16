# 用真实 Windows PowerShell 进程验证窄前置维护的精确差集、保留边界及恢复。
from __future__ import annotations

import ctypes
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

import pytest

from src.integrations.distribution.metadata import Distribution
from src.integrations.distribution.release_files import bootstrap_path, verify_release
from src.integrations.distribution.retired_files import reconcile_release


SCRIPT = Path(__file__).resolve().parents[2] / "tools/maintenance/Invoke-LegacyTakeover.ps1"


def sha(data):
    return hashlib.sha256(data).hexdigest()


def row(path, data):
    return dict(path=path, size=len(data), sha256=sha(data), bootstrap=bootstrap_path(path))


def write(root, path, data):
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)


@pytest.fixture
def fixture(tmp_path):
    source, target, backup = (tmp_path / name for name in ("new", "target", "backup"))
    distribution = Distribution(distribution="portable", update_channel="manual", version="2.3.1", build_id="new")
    payload = {"NTE_Drive_Calc.exe": b"new launcher", "_internal/runtime.dll": b"new runtime",
               "_internal/assets/Item.json": b"new asset", "distribution.json": json.dumps(asdict(distribution)).encode()}
    manifest = dict(schema_version=1, product="nte-drive-calc", build_id="new", version="2.3.1", architecture="x64",
                    files=[row(path, data) for path, data in payload.items()])
    for path, data in payload.items():
        write(source, path, data)
    write(source, "release-files.json", json.dumps(manifest).encode())
    shutil.copytree(source, target)
    retired = {"_internal/old.dll": b"old library", "_internal/dead.json": b"old asset"}
    old = {"NTE_Drive_Calc.exe": b"old launcher", "_internal/assets/Item.json": b"previous asset", **retired}
    for path, data in retired.items():
        write(target, path, data)
    profile = tmp_path / "old.json"
    profile.write_text(json.dumps(dict(schema_version=1, product="nte-drive-calc", profile_id="isolated-fixture",
                                     files=[row(path, data) for path, data in old.items()])))
    return dict(source=source, target=target, backup=backup, profile=profile, distribution=distribution, retired=retired)


def run(fixture, *, mode="Plan", backup=None, stopped=True, pin=None):
    command = ["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
               "-File", str(SCRIPT), "-Mode", mode, "-Profile", str(fixture["profile"]), "-ProfileSha256",
               pin or sha(fixture["profile"].read_bytes()), "-TargetRoot", str(fixture["target"]),
               "-NewPackageRoot", str(fixture["source"]), "-BackupRoot", str(backup or fixture["backup"])]
    if stopped:
        command.append("-ApplicationStopped")
    result = subprocess.run(command, capture_output=True, text=True, timeout=35)
    assert "ParserError" not in result.stderr, result.stderr
    return result


def success(result):
    assert result.returncode == 0, result.stdout + result.stderr
    start = result.stdout.index("{")
    return json.loads(result.stdout[start:])


def test_plan_apply_runtime_acceptance_and_exact_rollback(fixture):
    target = fixture["target"]
    preserved = {"accounts/a/user_data.sqlite3": b"account", "config/stats.json": b"user settings",
                 "userdata/accounts/a/user_data.sqlite3": b"portable account", "data/app_shared.sqlite3": b"shared facts",
                 "drivers/ViGEmBus_Setup.exe": b"never run", "notes.txt": b"user notes",
                 "unins000.exe": b"untrusted non-component", "unins000.dat": b"registration not claimed"}
    for path, data in preserved.items():
        write(target, path, data)
    before = {path: (target / path).read_bytes() for path in fixture["retired"] | preserved}
    plan = success(run(fixture))
    assert set(plan["retired"]) == set(fixture["retired"])
    assert not fixture["backup"].exists()
    assert all((target / path).read_bytes() == data for path, data in before.items())
    applied = success(run(fixture, mode="Apply"))
    backup = Path(applied["backup"])
    assert all(not (target / path).exists() for path in fixture["retired"])
    receipt = verify_release(target, fixture["distribution"])
    assert reconcile_release(target, target / "userdata/maintenance", receipt).first_acceptance
    assert success(run(fixture, mode="Apply"))["retired"] == []
    assert all((target / path).read_bytes() == data for path, data in preserved.items())
    restored = success(run(fixture, mode="Rollback", backup=backup))
    assert set(restored["restored"]) == set(fixture["retired"])
    assert success(run(fixture, mode="Rollback", backup=backup))["restored"] == []
    assert all((target / path).read_bytes() == data for path, data in before.items())
    assert (target / "NTE_Drive_Calc.exe").read_bytes() == b"new launcher"


@pytest.mark.parametrize("damage", ["partial_overlay", "source_hash", "old_changed", "unknown_dll", "root_core", "nested_uninstaller", "uninstaller_directory", "wrong_pin", "not_stopped"])
def test_preflight_failure_has_no_cleanup_or_backup(fixture, damage):
    target = fixture["target"]
    if damage == "partial_overlay":
        (target / "_internal/runtime.dll").unlink()
    elif damage == "source_hash":
        (fixture["source"] / "_internal/runtime.dll").write_bytes(b"bad source")
    elif damage == "old_changed":
        (target / "_internal/old.dll").write_bytes(b"modified")
    elif damage in {"unknown_dll", "root_core", "nested_uninstaller"}:
        path = {"unknown_dll": "_internal/unknown.dll", "root_core": "nte-core.exe", "nested_uninstaller": "_internal/unins000.exe"}[damage]
        write(target, path, b"not owned")
    elif damage == "uninstaller_directory":
        (target / "unins000.exe").mkdir()
    result = run(fixture, mode="Apply", pin="0" * 64 if damage == "wrong_pin" else None, stopped=damage != "not_stopped")
    assert result.returncode == 1, result.stdout
    assert (target / "_internal/old.dll").exists() and (target / "_internal/dead.json").exists()
    assert not fixture["backup"].exists()


@pytest.mark.parametrize("case", ["file", "directory"])
def test_windows_case_identity_keeps_current_member(fixture, case):
    target = fixture["target"]
    source = target / ("_internal/assets/Item.json" if case == "file" else "_internal")
    temporary = source.with_name("temporary-rename")
    destination = source.with_name("item.json" if case == "file" else "_INTERNAL")
    source.rename(temporary)
    temporary.rename(destination)
    result = success(run(fixture, mode="Apply"))
    assert set(result["retired"]) == {path.replace("_internal", "_INTERNAL") if case == "directory" else path for path in fixture["retired"]}
    assert (target / "_internal/assets/Item.json").read_bytes() == b"new asset"
    assert len(success(run(fixture, mode="Rollback", backup=Path(result["backup"])))["restored"]) == 2


@pytest.mark.parametrize("locked", ["_internal/old.dll", "NTE_Drive_Calc.exe"])
def test_occupied_file_blocks_before_backup(fixture, locked):
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p]
    kernel.CreateFileW.restype = ctypes.c_void_p
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel.CreateFileW(str(fixture["target"] / locked), 0x80000000, 0, None, 3, 0, None)
    assert handle != ctypes.c_void_p(-1).value
    try:
        result = run(fixture, mode="Apply")
        assert result.returncode == 1
        assert not fixture["backup"].exists()
        assert (fixture["target"] / "_internal/old.dll").exists()
    finally:
        kernel.CloseHandle(handle)


def test_parent_junction_and_linked_uninstaller_block(fixture, tmp_path):
    link = tmp_path / "alias"
    result = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(fixture["target"])], capture_output=True)
    assert result.returncode == 0
    original = fixture["target"]
    try:
        fixture["target"] = link
        assert run(fixture, mode="Apply").returncode == 1
        fixture["target"] = original
        nested = original / "unins000.exe"
        result = subprocess.run(["cmd", "/c", "mklink", "/J", str(nested), str(tmp_path)], capture_output=True)
        assert result.returncode == 0
        try:
            assert run(fixture, mode="Apply").returncode == 1
        finally:
            nested.rmdir()
        assert not fixture["backup"].exists()
    finally:
        link.rmdir()


@pytest.mark.parametrize("kind", ["traversal", "user_data", "duplicate_case"])
def test_even_pinned_invalid_profile_never_expands_owned_domain(fixture, kind):
    value = json.loads(fixture["profile"].read_text())
    if kind == "duplicate_case":
        value["files"].append(row("_internal/OLD.dll", b"old library"))
    else:
        value["files"].append(row("_internal/../outside.dll" if kind == "traversal" else "accounts/a/user_data.sqlite3", b"private"))
    fixture["profile"].write_text(json.dumps(value))
    assert run(fixture, mode="Apply").returncode == 1
    assert not fixture["backup"].exists()


def test_rollback_preflights_all_backups_and_refuses_overwrite(fixture):
    applied = success(run(fixture, mode="Apply"))
    backup = Path(applied["backup"])
    # 模拟中断已恢复一个成员：恢复幂等；后续有新内容时不覆盖该内容，也不先恢复其他成员。
    write(fixture["target"], "_internal/old.dll", b"another owner")
    assert run(fixture, mode="Rollback", backup=backup).returncode == 1
    assert not (fixture["target"] / "_internal/dead.json").exists()
    assert (fixture["target"] / "_internal/old.dll").read_bytes() == b"another owner"
    (fixture["target"] / "_internal/old.dll").write_bytes(b"old library")
    assert success(run(fixture, mode="Rollback", backup=backup))["restored"] == ["_internal/dead.json"]


@pytest.mark.parametrize("field,value", [("data_policy", "bad"), ("data_directory", "accounts"),
                                        ("data_root_key", "unexpected"), ("extra", "unknown"),
                                        ("schema_version", True), ("data_policy", "external")])
def test_formal_metadata_contract_precedes_retirement(fixture, field, value):
    source, target = fixture["source"], fixture["target"]
    metadata = json.loads((source / "distribution.json").read_bytes())
    metadata[field] = value
    payload = json.dumps(metadata).encode()
    manifest = json.loads((source / "release-files.json").read_bytes())
    manifest["files"] = [row("distribution.json", payload) if item["path"] == "distribution.json" else item for item in manifest["files"]]
    for root in (source, target):
        write(root, "distribution.json", payload)
        write(root, "release-files.json", json.dumps(manifest).encode())
    assert run(fixture, mode="Apply").returncode == 1
    assert not fixture["backup"].exists()
    assert (target / "_internal/old.dll").read_bytes() == b"old library"


def test_duplicate_json_manifest_key_rejected_before_retirement(fixture):
    for root in (fixture["source"], fixture["target"]):
        manifest = (root / "release-files.json").read_text()
        (root / "release-files.json").write_text(manifest.replace('"build_id": "new"', '"build_id": "old", "build_id": "new"'))
    assert run(fixture, mode="Apply").returncode == 1
    assert not fixture["backup"].exists()


@pytest.mark.parametrize("changed", ["manifest", "current_file"])
def test_rollback_binds_entire_current_package_not_only_exe(fixture, changed):
    backup = Path(success(run(fixture, mode="Apply"))["backup"])
    if changed == "manifest":
        path = fixture["target"] / "release-files.json"
        value = json.loads(path.read_bytes())
        value["build_id"] = "later-build-same-exe"
        path.write_text(json.dumps(value))
    else:
        (fixture["target"] / "_internal/runtime.dll").write_bytes(b"later runtime")
    assert run(fixture, mode="Rollback", backup=backup).returncode == 1
    assert all(not (fixture["target"] / path).exists() for path in fixture["retired"])


def test_interrupted_restore_uses_only_exact_journal_staging_and_publishes_whole_file(fixture):
    backup = Path(success(run(fixture, mode="Apply"))["backup"])
    journal = json.loads((backup / "journal.json").read_bytes())
    staging = fixture["target"] / "_internal" / f'.nte-recovery-{journal["recovery_id"]}-0.tmp'
    staging.write_bytes(b"interrupted half-copy")
    unrelated = staging.with_name(".nte-recovery-user-owned.tmp")
    unrelated.write_bytes(b"preserve unknown")
    success(run(fixture, mode="Rollback", backup=backup))
    assert not staging.exists()
    assert unrelated.read_bytes() == b"preserve unknown"
    assert all((fixture["target"] / path).read_bytes() == data for path, data in fixture["retired"].items())


@pytest.mark.parametrize("kind", ["oversize_metadata", "numeric_build", "utf8_bom", "utf16", "invalid_utf8"])
def test_json_encoding_and_limits_match_formal_reader_before_deletion(fixture, kind):
    source, target = fixture["source"], fixture["target"]
    metadata = (source / "distribution.json").read_bytes()
    manifest = json.loads((source / "release-files.json").read_bytes())
    if kind == "oversize_metadata":
        metadata += b" " * 65536
    elif kind == "numeric_build":
        value = json.loads(metadata)
        value["build_id"] = "1"
        metadata = json.dumps(value).encode()
        manifest["build_id"] = 1
    elif kind == "utf8_bom":
        metadata = b"\xef\xbb\xbf" + metadata
    elif kind == "utf16":
        metadata = metadata.decode().encode("utf-16")
    else:
        metadata = metadata.replace(b'"manual"', b'"manual\xff"')
    manifest["files"] = [row("distribution.json", metadata) if item["path"] == "distribution.json" else item for item in manifest["files"]]
    for root in (source, target):
        write(root, "distribution.json", metadata)
        write(root, "release-files.json", json.dumps(manifest).encode())
    assert run(fixture, mode="Apply").returncode == 1
    assert not fixture["backup"].exists()
    assert all((target / path).exists() for path in fixture["retired"])
