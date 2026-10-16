# 验证共享目录输入、单顶层 ZIP、清单及失败原子性，使用合成组件而不编译或安装。
from __future__ import annotations

import json
import os
from contextlib import nullcontext
import shutil
import stat
import zipfile

import pytest

from tests.release.portable_fixture import add_app_requirements
from tests.test_native_component_bundle_build import native_source, prepare
from tools.release import app_bundle_validation as common
from tools.release import build_portable as cli
from tools.release.onedir_inputs import inventory_onedir, verify_onedir_receipt, write_onedir_receipt
from tools.release.portable_archive import ARCHIVE_ROOT, build_portable_archive, validate_profile, verify_portable_archive


NTE_TEST_TIER = "core"


@pytest.fixture
def package_case(tmp_path, monkeypatch):
    root, _, _ = native_source(tmp_path)
    prepared = prepare(root)
    app = tmp_path / "中文 空格" / "NTE_Drive_Calc"
    shutil.copytree(prepared.resource_root, app / "_internal")
    add_app_requirements(app / "NTE_Drive_Calc.exe", app / "_internal")
    # OCR 模型有独立实际字节专项；此处验证每种成品都调用共同门禁，不加载后台。
    calls = []
    monkeypatch.setattr(common, "validate_packaged_ocr_models", lambda path: calls.append(path))
    receipt = tmp_path / "onedir-files.json"
    write_onedir_receipt(app, receipt, version="1.2.3")
    return {"app_dir": app, "receipt_path": receipt, "source_root": root,
            "output": tmp_path / "portable.zip", "version": "1.2.3"}, calls


def test_same_clean_onedir_has_two_distinct_portable_builds_and_runtime_manifest(package_case):
    case, calls = package_case
    before = inventory_onedir(case["app_dir"])
    first = build_portable_archive(**case, profile="kokodayo", previous_version="1.2.2",
        update_channel="platform", data_policy="external", data_root_key="kokodayo-ndc-stable")
    second = build_portable_archive(**{**case, "output": case["output"].with_name("second.zip")})
    assert first["build_id"] != second["build_id"]
    assert first["source_build_id"] == second["source_build_id"]
    assert inventory_onedir(case["app_dir"]) == before
    assert calls == [case["app_dir"] / "_internal"] * 2
    assert first["platform_entrypoint"] == "./NTE_Drive_Calc/NTE_Drive_Calc.exe"
    assert first["archive_bytes"] > 0 and first["expanded_bytes"] > 0
    with zipfile.ZipFile(case["output"]) as archive:
        assert all(name.startswith(ARCHIVE_ROOT + "/") for name in archive.namelist())
        assert not any("userdata" in name or "onedir-files" in name for name in archive.namelist())
        value = json.loads(archive.read(f"{ARCHIVE_ROOT}/distribution.json"))
        assert value["update_channel"] == "platform" and value["data_policy"] == "external"
        assert value["data_root_key"] == "kokodayo-ndc-stable"
    assert verify_portable_archive(case["output"])["sha256"] == first["sha256"]


@pytest.mark.parametrize("relative", ["userdata", "accounts", "logs", "_internal/scanned_images"])
def test_empty_runtime_directory_is_rejected_not_silently_excluded(package_case, relative):
    case, _ = package_case
    (case["app_dir"] / relative).mkdir()
    with pytest.raises(ValueError, match="运行目录"):
        build_portable_archive(**case)
    assert not case["output"].exists()


@pytest.mark.parametrize("relative", ["_internal/private.pdb", "_internal/native-capture/private.cpp",
    "_internal/src/app.py", "_internal/config/update_config.json", "_internal/config/private.json",
    "_internal/data/user_data.sqlite3", "_internal/data/game_static.sqlite3-wal", "_internal/NTE_SDK.bin"])
def test_sensitive_data_never_enters_receipt(package_case, relative):
    case, _ = package_case
    target = case["app_dir"] / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"do not publish")
    with pytest.raises(ValueError):
        write_onedir_receipt(case["app_dir"], case["receipt_path"], version=case["version"])
    assert not case["output"].exists()


@pytest.mark.parametrize("change", ["add", "modify", "missing", "version", "duplicate_json"])
def test_receipt_freezes_exact_members_bytes_and_version(package_case, change):
    case, _ = package_case
    target = case["app_dir"] / "NTE_Drive_Calc.exe"
    if change == "add":
        (case["app_dir"] / "_internal/new.txt").write_bytes(b"unapproved")
    elif change == "modify":
        target.write_bytes(b"changed")
    elif change == "missing":
        target.unlink()
    elif change == "version":
        case["version"] = "1.2.4"
    else:
        case["receipt_path"].write_text('{"schema_version":1,"schema_version":1}', encoding="utf-8")
    with pytest.raises(ValueError):
        build_portable_archive(**case)
    assert not case["output"].exists()


def test_required_notice_and_analysis_capability_are_shared_release_gates(package_case):
    case, _ = package_case
    license_file = case["app_dir"] / "_internal/licenses/analysis-core/LICENSE"
    license_file.unlink()
    write_onedir_receipt(case["app_dir"], case["receipt_path"], version=case["version"])
    with pytest.raises(RuntimeError, match="许可"):
        build_portable_archive(**case)
    license_file.write_text("public license", encoding="utf-8")
    manifest = case["app_dir"] / "_internal/analysis-core-meta/component.json"
    value = json.loads(manifest.read_text())
    capabilities = value["capabilities"]
    for missing in capabilities:
        value["capabilities"] = [name for name in capabilities if name != missing]
        manifest.write_text(json.dumps(value), encoding="utf-8")
        write_onedir_receipt(case["app_dir"], case["receipt_path"], version=case["version"])
        with pytest.raises(RuntimeError, match="分析组件"):
            build_portable_archive(**case)


def rewrite_archive(path, mutation):
    with zipfile.ZipFile(path) as archive:
        entries = [(info, archive.read(info)) for info in archive.infolist()]
    mutation(entries)
    changed = path.with_name("bad.zip")
    with zipfile.ZipFile(changed, "w") as archive:
        for info, data in entries:
            archive.writestr(info, data)
    return changed


@pytest.mark.parametrize("attack", ["duplicate", "case_duplicate", "traversal", "backslash", "symlink",
    "directory", "extra", "missing", "corrupt", "distribution", "absolute", "ads", "nested_root"])
def test_archive_rejects_unsafe_members_mixed_identity_and_corruption(package_case, attack):
    case, _ = package_case
    build_portable_archive(**case)

    def mutate(entries):
        if attack == "duplicate":
            entries.append(entries[0])
        elif attack == "case_duplicate":
            entries.append((zipfile.ZipInfo(entries[0][0].filename.upper()), entries[0][1]))
        elif attack in {"traversal", "backslash", "absolute", "ads", "nested_root"}:
            name = {"traversal": f"{ARCHIVE_ROOT}/../outside.txt", "backslash": f"{ARCHIVE_ROOT}\\x",
                    "absolute": "/outside", "ads": f"{ARCHIVE_ROOT}/_internal/a:stream",
                    "nested_root": f"wrapper/{ARCHIVE_ROOT}/x"}[attack]
            entries.append((zipfile.ZipInfo(name), b"x"))
        elif attack == "symlink":
            info = zipfile.ZipInfo(f"{ARCHIVE_ROOT}/_internal/link")
            info.create_system = 3
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            entries.append((info, b"outside"))
        elif attack == "directory":
            entries.append((zipfile.ZipInfo(f"{ARCHIVE_ROOT}/userdata/"), b""))
        elif attack == "extra":
            entries.append((zipfile.ZipInfo(f"{ARCHIVE_ROOT}/_internal/rogue.dll"), b"x"))
        elif attack == "missing":
            entries.pop(0)
        else:
            target = "distribution.json" if attack == "distribution" else "NTE_Drive_Calc.exe"
            for index, (info, data) in enumerate(entries):
                if info.filename.endswith("/" + target):
                    if attack == "distribution":
                        value = json.loads(data)
                        value["build_id"] = "different"
                        data = json.dumps(value).encode()
                    else:
                        data = b"x" * len(data)
                    entries[index] = (info, data)
    with pytest.warns(UserWarning) if attack == "duplicate" else nullcontext():
        bad = rewrite_archive(case["output"], mutate)
    with pytest.raises((ValueError, RuntimeError, zipfile.BadZipFile)):
        verify_portable_archive(bad)


def test_archive_total_size_and_truncation_are_bounded(package_case):
    case, _ = package_case
    build_portable_archive(**case)
    with pytest.raises(ValueError, match="压缩包"):
        verify_portable_archive(case["output"], max_compressed_bytes=1)
    with pytest.raises(ValueError, match="展开"):
        verify_portable_archive(case["output"], max_expanded_bytes=1)
    case["output"].write_bytes(case["output"].read_bytes()[:100])
    with pytest.raises(zipfile.BadZipFile):
        verify_portable_archive(case["output"])


def test_source_link_is_rejected_and_external_bytes_are_unchanged(package_case, tmp_path):
    case, _ = package_case
    outside = tmp_path / "outside"
    outside.write_bytes(b"external")
    try:
        os.symlink(outside, case["app_dir"] / "_internal/link.txt")
    except OSError:
        pytest.skip("当前 Windows 测试进程未获得 symlink 创建能力")
    with pytest.raises(ValueError, match="链接|重解析"):
        build_portable_archive(**case)
    assert outside.read_bytes() == b"external"


def test_failed_copy_keeps_previous_zip_and_removes_only_own_stage(package_case, monkeypatch):
    case, _ = package_case
    previous = case["output"].with_name("previous.zip")
    previous.write_bytes(b"prior release")
    monkeypatch.setattr("tools.release.portable_archive.shutil.copyfile", lambda *_: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError, match="disk full"):
        build_portable_archive(**case)
    assert previous.read_bytes() == b"prior release" and not case["output"].exists()
    assert not list(case["output"].parent.glob(".portable-stage-*"))
    case["output"].write_bytes(b"existing output")
    with pytest.raises(ValueError, match="已存在"):
        build_portable_archive(**case)
    assert case["output"].read_bytes() == b"existing output"


@pytest.mark.parametrize("version,previous", [("1.2.3.T261015", None), ("v1.2.3", None), ("1.2", None),
    ("1.2.3", "1.2.3"), ("1.2.3", "1.3.0"), ("1.2.3", "invalid")])
def test_kokodayo_profile_preserves_source_version_and_requires_increasing_triplet(version, previous):
    with pytest.raises(ValueError):
        validate_profile(version, "kokodayo", previous)
    validate_profile("1.2.3.T261015", "manual")


def test_cli_only_packages_existing_bundle_and_emits_external_receipt(package_case, tmp_path, monkeypatch, capsys):
    case, _ = package_case
    output_root = case["source_root"] / "build/candidate"
    monkeypatch.setattr(cli, "ROOT", case["source_root"])
    monkeypatch.setattr(cli, "__version__", case["version"])
    assert cli.main(["--output-root", str(output_root), "--app-dir", str(case["app_dir"]),
        "--input-receipt", str(case["receipt_path"]), "--profile", "kokodayo",
        "--update-channel", "platform", "--data-policy", "external", "--data-root-key", "kokodayo-ndc-stable"]) == 0
    artifacts = list((output_root / "dist/portable").glob("*.zip"))
    assert len(artifacts) == 1
    assert artifacts[0].with_suffix(".zip.sha256").is_file()
    assert artifacts[0].with_suffix(".zip.json").is_file()
    assert "未提供平台旧版本" in capsys.readouterr().out
    verify_onedir_receipt(case["app_dir"], case["receipt_path"], version=case["version"])


@pytest.mark.parametrize("profile", ["manual", "kokodayo"])
def test_platform_external_package_is_reachable_and_contains_no_machine_path(package_case, profile):
    case, _ = package_case
    summary = build_portable_archive(**case, profile=profile, update_channel="platform",
        data_policy="external", data_root_key="kokodayo-ndc-stable")
    assert summary["update_channel"] == "platform" and summary["data_policy"] == "external"
    assert summary["data_root_key"] == "kokodayo-ndc-stable"
    if profile == "kokodayo":
        assert summary["platform_install_directory_policy"] == "delete_then_extract_static_client_0.1.7"
    with zipfile.ZipFile(case["output"]) as archive:
        raw = archive.read(f"{ARCHIVE_ROOT}/distribution.json").decode()
        assert "kokodayo-ndc-stable" in raw
        assert str(case["app_dir"]) not in raw and "LOCALAPPDATA" not in raw
    assert verify_portable_archive(case["output"], profile=profile)["data_policy"] == "external"


def test_kokodayo_rejects_internal_data_before_touching_build_inputs(package_case):
    case, calls = package_case
    with pytest.raises(ValueError, match="external"):
        build_portable_archive(**case, profile="kokodayo", update_channel="platform", data_policy="userdata")
    assert calls == [] and not case["output"].exists()


def test_generic_platform_profile_preserves_explicit_userdata_policy(package_case):
    case, _ = package_case
    summary = build_portable_archive(**case, profile="manual", update_channel="platform", data_policy="userdata")
    assert summary["data_policy"] == "userdata"
    assert verify_portable_archive(case["output"])["data_policy"] == "userdata"
    with pytest.raises(ValueError, match="external"):
        verify_portable_archive(case["output"], profile="kokodayo")


@pytest.mark.parametrize("options", [
    {"profile": "kokodayo"},
    {"profile": "kokodayo", "update_channel": "platform"},
    {"profile": "kokodayo", "update_channel": "platform", "data_policy": "external"},
    {"update_channel": "platform", "data_policy": "external"},
    {"update_channel": "platform", "data_policy": "external", "data_root_key": "C:/private/data"},
    {"update_channel": "manual", "data_policy": "external", "data_root_key": "stable"},
])
def test_platform_channel_and_explicit_data_policy_are_never_guessed(package_case, options):
    case, _ = package_case
    with pytest.raises((ValueError, RuntimeError)):
        build_portable_archive(**case, **options)
    assert not case["output"].exists()
