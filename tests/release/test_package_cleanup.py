# 验证打包清理只删除三个确切普通输出，整体预检和逐次复核均保留无关数据。
from pathlib import Path
import os
import stat
import subprocess
from unittest.mock import Mock

import pytest

import build_exe


@pytest.fixture
def package_root(tmp_path, monkeypatch):
    root = tmp_path / "package-root"
    root.mkdir()
    for name, path in {
        "ROOT": root,
        "BUILD": root / "build",
        "DIST": root / "dist",
        "PACKAGE_BUILD_DIR": root / "build/NTE_Drive_Calc",
        "PACKAGE_ONEDIR_DIR": root / "dist/NTE_Drive_Calc",
        "PACKAGE_ONEFILE_EXE": root / "dist/NTE_Drive_Calc.exe",
    }.items():
        monkeypatch.setattr(build_exe, name, path)
    # 所有用例都禁止构建；合法清理用例只触及当前临时根的确切成员。
    monkeypatch.setattr(build_exe.PyInstaller.__main__, "run", Mock(side_effect=AssertionError("no compiler")))
    return root


def _targets():
    return (build_exe.PACKAGE_BUILD_DIR, build_exe.PACKAGE_ONEDIR_DIR, build_exe.PACKAGE_ONEFILE_EXE)


def test_removes_only_owned_outputs_and_accepts_absent_members(package_root):
    build_exe._preflight_package_cleanup()
    for path in _targets():
        build_exe._remove_package_artifact(path)
    for path in _targets()[:2]:
        path.mkdir(parents=True)
        (path / "owned.bin").write_bytes(b"owned output")
    build_exe.PACKAGE_ONEFILE_EXE.write_bytes(b"owned executable")
    unrelated = (package_root / "build/other-task/keep", package_root / "dist/other.exe")
    for path in unrelated:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"unrelated retained")
    build_exe._preflight_package_cleanup()
    for path in _targets():
        assert path.resolve(strict=True).is_relative_to(package_root.resolve(strict=True))
        build_exe._remove_package_artifact(path)
    assert all(not path.exists() for path in _targets())
    assert all(path.read_bytes() == b"unrelated retained" for path in unrelated)


@pytest.mark.parametrize("invalid", ["root", "unowned", "directory_as_file", "file_as_directory", "missing_root"])
def test_rejects_unknown_identity_type_and_missing_root(package_root, monkeypatch, invalid):
    target = build_exe.PACKAGE_ONEFILE_EXE
    if invalid == "root":
        target = package_root
    elif invalid == "unowned":
        target = package_root / "other-output"
        target.write_bytes(b"keep")
    elif invalid == "directory_as_file":
        target.mkdir(parents=True)
        (target / "keep").write_bytes(b"keep")
    elif invalid == "file_as_directory":
        target = build_exe.PACKAGE_BUILD_DIR
        target.parent.mkdir()
        target.write_bytes(b"keep")
    else:
        package_root.rmdir()  # 本次空夹具，不是生产根。
    remove = Mock(side_effect=AssertionError("no recursive deletion"))
    unlink = Mock(side_effect=AssertionError("no file deletion"))
    with monkeypatch.context() as guard:
        guard.setattr(build_exe.shutil, "rmtree", remove)
        guard.setattr(Path, "unlink", unlink)
        with pytest.raises((ValueError, FileNotFoundError)):
            build_exe._remove_package_artifact(target)
    remove.assert_not_called()
    unlink.assert_not_called()


def test_main_preflights_all_outputs_before_preparation_or_deletion(package_root, monkeypatch):
    first = build_exe.PACKAGE_BUILD_DIR
    first.mkdir(parents=True)
    sentinel = first / "keep"
    sentinel.write_bytes(b"previous build")
    build_exe.PACKAGE_ONEFILE_EXE.mkdir(parents=True)  # 最后一项类型错误。
    prepare = Mock(side_effect=AssertionError("preparation must not start"))
    remove = Mock(side_effect=AssertionError("cleanup must not start"))
    monkeypatch.setattr(build_exe, "load_backend_support", prepare)
    monkeypatch.setattr(build_exe, "_remove_package_artifact", remove)
    with pytest.raises(ValueError, match="类型"):
        build_exe.main()
    prepare.assert_not_called()
    remove.assert_not_called()
    build_exe.PyInstaller.__main__.run.assert_not_called()
    assert sentinel.read_bytes() == b"previous build"


def test_each_removal_rechecks_after_successful_preflight(package_root, monkeypatch):
    build_exe._preflight_package_cleanup()
    target = build_exe.PACKAGE_ONEFILE_EXE
    target.mkdir(parents=True)
    sentinel = target / "keep"
    sentinel.write_bytes(b"changed identity")
    remove = Mock(side_effect=AssertionError("must not recurse into new directory"))
    monkeypatch.setattr(build_exe.shutil, "rmtree", remove)
    with pytest.raises(ValueError, match="类型"):
        build_exe._remove_package_artifact(target)
    remove.assert_not_called()
    assert sentinel.read_bytes() == b"changed identity"


def _junction(link: Path, destination: Path, fixture: Path):
    # 两端只在本用例根内；不指向仓库 dist、build 或任意真实产物。
    assert link.absolute().is_relative_to(fixture.absolute())
    assert destination.resolve(strict=True).is_relative_to(fixture.resolve(strict=True))
    if os.name == "nt":
        result = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(destination)],
                                capture_output=True, text=True, encoding="utf-8", errors="replace")
        assert result.returncode == 0, result.stdout + result.stderr
    else:
        link.symlink_to(destination, target_is_directory=True)


@pytest.mark.parametrize("redirect", ["root", "build_parent", "dist_parent", "leaf", "dangling_leaf"])
def test_redirects_fail_closed_without_touching_target(package_root, tmp_path, monkeypatch, redirect):
    build_exe._preflight_package_cleanup()
    external = tmp_path / "other-owner"
    external.mkdir()
    sentinel = external / "keep"
    sentinel.write_bytes(b"other owner")
    if redirect == "root":
        package_root.rmdir()
        link = package_root
    elif redirect == "build_parent":
        link = package_root / "build"
    elif redirect == "dist_parent":
        link = package_root / "dist"
    else:
        link = build_exe.PACKAGE_BUILD_DIR
        link.parent.mkdir()
    destination = external
    if redirect == "dangling_leaf":
        destination = external / "absent-after-link"
        destination.mkdir()
    _junction(link, destination, tmp_path)
    if redirect == "dangling_leaf":
        destination.rmdir()  # 空目录离开后，lstat 仍须识别残留联接。
    remove = Mock(side_effect=AssertionError("no redirected deletion"))
    unlink = Mock(side_effect=AssertionError("no redirected unlink"))
    try:
        with monkeypatch.context() as guard:
            guard.setattr(build_exe.shutil, "rmtree", remove)
            guard.setattr(Path, "unlink", unlink)
            with pytest.raises(ValueError, match="重解析点"):
                build_exe._preflight_package_cleanup()
            for path in _targets():
                # dist 重定向不影响 build 身份；其余两项必须独立复核。
                if redirect == "dist_parent" and path == build_exe.PACKAGE_BUILD_DIR:
                    continue
                if redirect in {"build_parent", "leaf", "dangling_leaf"} and path != build_exe.PACKAGE_BUILD_DIR:
                    continue
                with pytest.raises(ValueError, match="重解析点"):
                    build_exe._remove_package_artifact(path)
        remove.assert_not_called()
        unlink.assert_not_called()
        assert sentinel.read_bytes() == b"other owner"
    finally:
        # 只移除本用例创建的链接本身；不递归删除它所指向的目录。
        info = link.lstat()
        assert stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
        if os.name == "nt":
            link.rmdir()
        else:
            link.unlink()


def test_path_access_failure_is_not_treated_as_missing(package_root, monkeypatch):
    original = build_exe.plain_member_stat
    def denied(root, member):
        if member == build_exe.PACKAGE_ONEFILE_EXE:
            raise PermissionError(13, "synthetic access check failed")
        return original(root, member)
    monkeypatch.setattr(build_exe, "plain_member_stat", denied)
    with pytest.raises(PermissionError):
        build_exe._remove_package_artifact(build_exe.PACKAGE_ONEFILE_EXE)
