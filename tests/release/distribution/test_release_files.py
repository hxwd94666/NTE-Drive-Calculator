# 用隔离发行包验证整包门禁、可信旧清单退役与未知文件保留。
from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import subprocess

import pytest

from src.integrations.distribution.metadata import Distribution
from src.integrations.distribution import release_files as files
from src.integrations.distribution.release_files import ReleaseValidationError, verify_release
from src.integrations.distribution.retired_files import accept_release, reconcile_release


def package(root, *, build="b1", content=None):
    root.mkdir(parents=True, exist_ok=True)
    distribution = Distribution(distribution="portable", update_channel="manual", version="1.2.3", build_id=build)
    payloads = {"NTE_Drive_Calc.exe": b"launcher", "distribution.json": json.dumps(asdict(distribution)).encode(),
                "_internal/runtime.dll": b"runtime", **(content or {})}
    rows = []
    for relative, data in payloads.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        rows.append({"path": relative, "size": len(data), "sha256": hashlib.sha256(data).hexdigest(),
                     "bootstrap": files.bootstrap_path(relative)})
    manifest = {"schema_version": 1, "product": "nte-drive-calc", "version": distribution.version,
                "build_id": build, "architecture": "x64", "files": rows}
    (root / "release-files.json").write_text(json.dumps(manifest), encoding="utf-8")
    return distribution, manifest


def accepted_package(tmp_path, content=None):
    root = tmp_path / "app"
    distribution, manifest = package(root, content=content)
    maintenance = root / "userdata" / "maintenance"
    receipt = verify_release(root, distribution)
    assert reconcile_release(root, maintenance, receipt).first_acceptance
    accept_release(maintenance, receipt)
    return root, maintenance, distribution, manifest


def test_clean_package_acceptance_and_data_survives(tmp_path):
    root, maintenance, distribution, _ = accepted_package(tmp_path)
    user = root / "userdata" / "accounts" / "frozen.db"
    user.parent.mkdir()
    user.write_bytes(b"user facts")
    receipt = verify_release(root, distribution)
    result = reconcile_release(root, maintenance, receipt)
    assert not result.first_acceptance and result.removed == ()
    assert user.read_bytes() == b"user facts"


@pytest.mark.parametrize("damage", ["missing", "wrong_size", "same_size", "metadata", "manifest_build"])
def test_incomplete_package_rejected_before_acceptance(tmp_path, damage):
    distribution, manifest = package(tmp_path)
    target = tmp_path / "_internal/runtime.dll"
    if damage == "missing":
        target.unlink()
    elif damage == "wrong_size":
        target.write_bytes(b"x")
    elif damage == "same_size":
        target.write_bytes(b"RUNTIME")
    elif damage == "metadata":
        (tmp_path / "distribution.json").write_text("{}")
    else:
        manifest["build_id"] = "another"
        (tmp_path / "release-files.json").write_text(json.dumps(manifest))
    with pytest.raises(ReleaseValidationError):
        verify_release(tmp_path, distribution)
    assert not (tmp_path / "userdata").exists()


@pytest.mark.parametrize("path", ["../outside", "_internal/../secret", "C:/bad", "_internal\\bad", "_internal/CON.txt",
                                  "_internal/a.", "_internal/a ", "userdata/owner", "_internal/a:stream"])
def test_manifest_rejects_windows_alias_and_data_paths(tmp_path, path):
    _, manifest = package(tmp_path)
    manifest["files"][0]["path"] = path
    with pytest.raises(ReleaseValidationError):
        files.parse_release_manifest(manifest)


@pytest.mark.parametrize("collision", ["case", "parent", "bootstrap", "directory_case"])
def test_manifest_case_prefix_and_false_bootstrap_rejected(tmp_path, collision):
    _, manifest = package(tmp_path)
    if collision == "bootstrap":
        manifest["files"][0]["bootstrap"] = False
    elif collision == "directory_case":
        for name in ("_internal/Dir/a.json", "_internal/dir/b.json"):
            manifest["files"].append(dict(manifest["files"][-1], path=name, bootstrap=False))
    else:
        extra = dict(manifest["files"][-1], path="_internal/RUNTIME.dll" if collision == "case" else "_internal/runtime.dll/child")
        extra["bootstrap"] = files.bootstrap_path(extra["path"])
        manifest["files"].append(extra)
    with pytest.raises(ReleaseValidationError):
        files.parse_release_manifest(manifest)


def test_successful_overlay_removes_only_exact_old_nonbootstrap(tmp_path):
    root, maintenance, _, _ = accepted_package(tmp_path, {"_internal/retired.json": b"old asset"})
    retained = root / "userdata/user.txt"
    retained.write_bytes(b"keep")
    (root / "readme-user.txt").write_text("keep")
    platform_dir = root / "platform-metadata"
    platform_dir.mkdir()
    (platform_dir / "state.json").write_text("keep")
    distribution, _ = package(root, build="b2", content={"_internal/new.json": b"new asset"})
    receipt = verify_release(root, distribution)
    result = reconcile_release(root, maintenance, receipt)
    assert result.removed == ("_internal/retired.json",)
    assert not (root / "_internal/retired.json").exists()
    assert retained.read_bytes() == b"keep"
    assert (root / "readme-user.txt").read_text() == "keep"
    assert (platform_dir / "state.json").read_text() == "keep"
    accept_release(maintenance, receipt)
    assert reconcile_release(root, maintenance, receipt).removed == ()


@pytest.mark.parametrize("case", ["unknown", "modified", "bootstrap", "tampered_record"])
def test_loading_risk_or_modified_retired_preserves_everything(tmp_path, case):
    root, maintenance, _, _ = accepted_package(tmp_path, {"_internal/old.json": b"old", "_internal/old.dll": b"old dll"})
    distribution, _ = package(root, build="b2")
    if case != "bootstrap":
        (root / "_internal/old.dll").unlink()
    if case == "unknown":
        (root / "_internal/foreign.json").write_bytes(b"unknown")
    elif case == "modified":
        (root / "_internal/old.json").write_bytes(b"custom")
    elif case == "tampered_record":
        record = maintenance / "accepted-release.json"
        value = json.loads(record.read_text())
        value["manifest"]["build_id"] = "tampered"
        record.write_text(json.dumps(value))
    before = (root / "_internal/old.json").read_bytes()
    with pytest.raises(ReleaseValidationError):
        reconcile_release(root, maintenance, verify_release(root, distribution))
    assert (root / "_internal/old.json").read_bytes() == before
    assert json.loads((maintenance / "accepted-release.json").read_text())["manifest"]["build_id"] != "b2"


@pytest.mark.parametrize("extra", ["old.dll", "_internal/old.dat", "_internal/foreign.py"])
def test_first_takeover_does_not_claim_unowned_files(tmp_path, extra):
    distribution, _ = package(tmp_path)
    (tmp_path / extra).write_bytes(b"legacy")
    with pytest.raises(ReleaseValidationError):
        reconcile_release(tmp_path, tmp_path / "userdata/maintenance", verify_release(tmp_path, distribution))
    assert (tmp_path / extra).read_bytes() == b"legacy"
    assert not (tmp_path / "userdata").exists()


def test_receipt_rechecks_without_rehashing_package(tmp_path, monkeypatch):
    distribution, _ = package(tmp_path)
    receipt = verify_release(tmp_path, distribution)
    monkeypatch.setattr(files, "file_sha256", lambda _p: pytest.fail("package hashed twice"))
    maintenance = tmp_path / "userdata/maintenance"
    reconcile_release(tmp_path, maintenance, receipt)
    accept_release(maintenance, receipt)
    (tmp_path / "_internal/runtime.dll").write_bytes(b"changed")
    with pytest.raises(ReleaseValidationError, match="改变"):
        reconcile_release(tmp_path, maintenance, receipt)
    with pytest.raises(ReleaseValidationError, match="改变"):
        accept_release(maintenance, receipt)


def test_manifest_change_after_verification_rejected(tmp_path):
    distribution, _ = package(tmp_path)
    receipt = verify_release(tmp_path, distribution)
    (tmp_path / "release-files.json").write_text("{}")
    with pytest.raises(ReleaseValidationError):
        accept_release(tmp_path / "userdata/maintenance", receipt)


def test_reparse_ancestor_rejected_without_following_target(tmp_path, monkeypatch):
    distribution, _ = package(tmp_path)
    original = Path.lstat
    import stat
    from types import SimpleNamespace

    def lstat(path):
        if path == tmp_path:
            return SimpleNamespace(st_mode=stat.S_IFDIR, st_file_attributes=stat.FILE_ATTRIBUTE_REPARSE_POINT)
        return original(path)
    monkeypatch.setattr(Path, "lstat", lstat)
    with pytest.raises(ReleaseValidationError, match="重解析"):
        verify_release(tmp_path, distribution)


def test_legacy_unchanged_but_orphaned_manifest_rejected(tmp_path):
    assert verify_release(tmp_path, Distribution()) is None
    package(tmp_path)
    with pytest.raises(ReleaseValidationError, match="标识丢失"):
        verify_release(tmp_path, Distribution())


def test_same_build_id_different_content_rejected(tmp_path):
    root, maintenance, _, _ = accepted_package(tmp_path)
    distribution, _ = package(root, content={"_internal/new.json": b"new"})
    with pytest.raises(ReleaseValidationError, match="build_id"):
        reconcile_release(root, maintenance, verify_release(root, distribution))


def test_busy_retirement_stops_before_acceptance(tmp_path, monkeypatch):
    root, maintenance, _, _ = accepted_package(tmp_path, {"_internal/old.json": b"old"})
    distribution, _ = package(root, build="b2")
    original = Path.unlink
    def unlink(path, *args, **kwargs):
        if path.name == "old.json":
            raise PermissionError("in use")
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "unlink", unlink)
    with pytest.raises(ReleaseValidationError, match="维护失败"):
        reconcile_release(root, maintenance, verify_release(root, distribution))
    assert (root / "_internal/old.json").read_bytes() == b"old"
    assert json.loads((maintenance / "accepted-release.json").read_text())["manifest"]["build_id"] == "b1"


def test_real_directory_link_preserves_external_target(tmp_path):
    outside = tmp_path / "outside"
    root = tmp_path / "app"
    distribution, _ = package(root)
    outside.mkdir()
    marker = outside / "private.json"
    marker.write_bytes(b"not package owned")
    link = root / "_internal/linked"
    if os.name == "nt":
        result = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(outside)], capture_output=True)
        assert result.returncode == 0, result.stderr
    else:
        link.symlink_to(outside, target_is_directory=True)
    try:
        with pytest.raises(ReleaseValidationError, match="重解析"):
            reconcile_release(root, root / "userdata/maintenance", verify_release(root, distribution))
        assert marker.read_bytes() == b"not package owned"
    finally:
        # 精确移除本测试创建的链接入口，不递归遍历目标。
        if os.name == "nt":
            link.rmdir()
        else:
            link.unlink()


@pytest.mark.parametrize("changed,parent_case", [(False, False), (True, False), (True, True)])
def test_windows_case_only_overlay_never_retires_current_file(tmp_path, changed, parent_case):
    old = "_internal/Assets/item.json" if parent_case else "_internal/item.json"
    new = "_internal/assets/Item.json" if parent_case else "_internal/Item.json"
    root, maintenance, _, _ = accepted_package(tmp_path, {old: b"original"})
    distribution, _ = package(root, build="b2", content={new: b"modified" if changed else b"original"})
    receipt = verify_release(root, distribution)
    if os.name != "nt":
        # 大小写敏感盘上是两个物理文件，不允许带冲突盘面发行到 Windows。
        with pytest.raises(ReleaseValidationError, match="大小写冲突"):
            reconcile_release(root, maintenance, receipt)
        return
    result = reconcile_release(root, maintenance, receipt)
    assert result.removed == ()
    assert (root / new).read_bytes() == (b"modified" if changed else b"original")
    accept_release(maintenance, receipt)
    assert reconcile_release(root, maintenance, receipt).removed == ()


def test_windows_internal_directory_case_does_not_hide_unknown_loading_files(tmp_path):
    distribution, _ = package(tmp_path)
    original = tmp_path / "_internal"
    temporary = tmp_path / "rename-internal"
    original.rename(temporary)
    temporary.rename(tmp_path / "_INTERNAL")
    (tmp_path / "_INTERNAL/foreign.dll").write_bytes(b"foreign")
    with pytest.raises(ReleaseValidationError):
        # Windows 清单读取可成功，但盘面扫描仍须看到大写目录内的加载风险。
        reconcile_release(tmp_path, tmp_path / "userdata/maintenance", verify_release(tmp_path, distribution))
    assert (tmp_path / "_INTERNAL/foreign.dll").read_bytes() == b"foreign"


@pytest.mark.parametrize("name", ["unins000.exe", "UNINS000.EXE", "unins001.exe", "nte-core.exe", "_internal/unins000.exe"])
def test_only_exact_root_uninstaller_is_unmanaged_and_preserved(tmp_path, name):
    distribution, _ = package(tmp_path)
    path = tmp_path / name
    path.write_bytes(b"not trusted; not executed")
    receipt = verify_release(tmp_path, distribution)
    if name.casefold() == "unins000.exe":
        assert reconcile_release(tmp_path, tmp_path / "userdata/maintenance", receipt).first_acceptance
    else:
        with pytest.raises(ReleaseValidationError):
            reconcile_release(tmp_path, tmp_path / "userdata/maintenance", receipt)
    assert path.read_bytes() == b"not trusted; not executed"


def test_unmanaged_uninstaller_directory_is_not_a_regular_file(tmp_path):
    distribution, _ = package(tmp_path)
    (tmp_path / "unins000.exe").mkdir()
    with pytest.raises(ReleaseValidationError):
        reconcile_release(tmp_path, tmp_path / "userdata/maintenance", verify_release(tmp_path, distribution))
