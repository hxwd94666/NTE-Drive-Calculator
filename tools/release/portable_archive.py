# 从单次构建白名单制作单顶层便携 ZIP，逐成员核验并在完整成功后发布产物。
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import shutil
import stat
import tempfile
from uuid import uuid4
import zipfile

from tools.release.app_bundle_validation import validate_app_bundle
from tools.release.game_component_bundle_build import source_component_manifest
from tools.release.managed_paths import plain_directory_root, plain_member_stat
from tools.release.onedir_inputs import APP_EXE, sha256, unique_object, verify_onedir_receipt


ARCHIVE_ROOT = "NTE_Drive_Calc"
MAX_EXPANDED_BYTES = 4 * 1024**3
MAX_ARCHIVE_MEMBERS = 100_000
MAX_METADATA_BYTES = 16 * 1024**2
KOKODAYO_MAX_BYTES = 1_000_000_000  # 官方只写 1GB，保守按十进制上限，不自行假设 GiB。


def validate_profile(version: str, profile: str, previous_version: str | None = None) -> None:
    if profile not in {"manual", "kokodayo"}:
        raise ValueError("未知便携发行 profile")
    if profile == "kokodayo":
        if re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version) is None:
            raise ValueError("KoKodayo 版本必须为三段数字；不自动删除测试标识")
        if previous_version is not None:
            if re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", previous_version) is None:
                raise ValueError("KoKodayo 旧版本必须为三段数字")
            if tuple(map(int, version.split("."))) <= tuple(map(int, previous_version.split("."))):
                raise ValueError("KoKodayo 更新版本必须严格递增")
    elif previous_version is not None:
        raise ValueError("previous_version 只用于已确认的平台版本门禁")


def portable_distribution(version: str, build_id: str, *, update_channel="manual", data_policy="userdata", data_root_key="") -> dict:
    from src.integrations.distribution.metadata import parse_distribution
    value = {"schema_version": 1, "product": "nte-drive-calc", "distribution": "portable",
            "update_channel": update_channel, "version": version, "build_id": build_id, "architecture": "x64",
            "entrypoint": APP_EXE, "release_manifest": "release-files.json", "data_policy": "userdata",
            "data_directory": "userdata"}
    value["data_policy"] = data_policy
    if data_root_key:
        value["data_root_key"] = data_root_key
    parse_distribution(value)
    return value


def _validate_profile_policy(profile: str, update_channel: str, data_policy: str | None) -> None:
    if profile == "kokodayo":
        if update_channel != "platform":
            raise ValueError("KoKodayo 候选必须显式选择 platform 更新渠道")
        # 官方 0.1.7 客户端静态调用链先删除目标安装目录再解包；数据不得随程序根存放。
        if data_policy != "external":
            raise ValueError("KoKodayo 安装会重建程序目录，必须选择 external 数据策略和稳定 data_root_key")
    if update_channel == "platform" and data_policy is None:
        raise ValueError("平台候选必须显式选择 userdata 或 external 数据策略")


def _metadata_bytes(value: dict) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _archive_member(info: zipfile.ZipInfo) -> str:
    """ZIP 名称和 Windows 展开身份必须一致；目录项本身也不能带入用户目录或链接。"""
    name = info.filename
    if info.orig_filename != name or "\x00" in name or "\\" in name or ":" in name:
        raise ValueError("ZIP 成员名称无效")
    parts = name.split("/")
    if info.is_dir() or not parts or parts[0] != ARCHIVE_ROOT or len(parts) < 2:
        raise ValueError("ZIP 必须使用单一固定顶层目录，且只声明文件成员")
    if any(part in {"", ".", ".."} or part.endswith((".", " ")) for part in parts):
        raise ValueError("ZIP 成员路径越界或存在 Windows 别名")
    mode = (info.external_attr >> 16) & 0xFFFF
    if stat.S_IFMT(mode) not in {0, stat.S_IFREG} or info.external_attr & 0x400:
        raise ValueError("ZIP 链接、重解析点或非普通文件被拒绝")
    if info.flag_bits & 1:
        raise ValueError("发行 ZIP 不接受加密成员")
    return "/".join(parts[1:])


def verify_portable_archive(path: Path, *, max_compressed_bytes: int = 4 * 1024**3,
                            max_expanded_bytes: int = MAX_EXPANDED_BYTES, profile: str = "manual") -> dict:
    """有界流式验证，不解压；内容校验复用运行时正式清单协议。"""
    from src.integrations.distribution.release_files import bootstrap_path, parse_release_manifest
    from src.integrations.distribution.metadata import parse_distribution
    limit = min(max_compressed_bytes, KOKODAYO_MAX_BYTES) if profile == "kokodayo" else max_compressed_bytes
    if path.stat().st_size > limit:
        raise ValueError("ZIP 超出压缩包体积上限")
    with zipfile.ZipFile(path) as archive:
        entries, folded, total = {}, set(), 0
        if len(archive.infolist()) > MAX_ARCHIVE_MEMBERS:
            raise ValueError("ZIP 成员数量超限")
        for info in archive.infolist():
            relative = _archive_member(info)
            if relative.casefold() in folded:
                raise ValueError("ZIP 重复成员或大小写冲突")
            folded.add(relative.casefold())
            entries[relative] = info
            total += info.file_size
            if total > max_expanded_bytes:
                raise ValueError("ZIP 展开体积超限")
        for metadata in ("distribution.json", "release-files.json"):
            if metadata not in entries or entries[metadata].file_size > MAX_METADATA_BYTES:
                raise ValueError("ZIP 缺少有界发行清单")
        manifest = parse_release_manifest(json.loads(archive.read(entries["release-files.json"]), object_pairs_hook=unique_object))
        distribution = parse_distribution(json.loads(archive.read(entries["distribution.json"]), object_pairs_hook=unique_object))
        validate_profile(distribution.version, profile)
        _validate_profile_policy(profile, distribution.update_channel, distribution.data_policy)
        if (not distribution.portable or any(getattr(distribution, field) != getattr(manifest, field)
                for field in ("version", "build_id", "architecture", "product"))):
            raise ValueError("ZIP 发行类型、路径或构建身份与清单不匹配")
        expected = {member.path: member for member in manifest.files}
        if set(entries) != {*expected, "release-files.json"}:
            raise ValueError("ZIP 成员与正式发行清单不一致")
        for relative, member in expected.items():
            info = entries[relative]
            if info.file_size != member.size or member.bootstrap != bootstrap_path(relative):
                raise ValueError(f"ZIP 成员大小或引导身份不符：{relative}")
            hasher, actual_size = hashlib.sha256(), 0
            with archive.open(info) as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    actual_size += len(block)
                    if actual_size > member.size:
                        raise ValueError("ZIP 实际展开数据超出清单")
                    hasher.update(block)
            if actual_size != member.size or hasher.hexdigest() != member.sha256:
                raise ValueError(f"ZIP 成员哈希不符：{relative}")
    return {"version": manifest.version, "build_id": manifest.build_id, "architecture": manifest.architecture,
            "file_count": len(entries), "expanded_bytes": total, "archive_bytes": path.stat().st_size,
            "sha256": sha256(path), "archive_root": ARCHIVE_ROOT,
            "platform_entrypoint": f"./{ARCHIVE_ROOT}/{APP_EXE}",
            "update_channel": distribution.update_channel, "data_policy": distribution.data_policy,
            "data_root_key": distribution.data_root_key}


def build_portable_archive(*, app_dir: Path, receipt_path: Path, output: Path, source_root: Path,
                           version: str, profile: str = "manual", previous_version: str | None = None,
                           update_channel: str = "manual", data_policy: str | None = None, data_root_key: str = "") -> dict:
    from src.integrations.distribution.metadata import load_distribution
    from src.integrations.distribution.release_files import bootstrap_path, parse_release_manifest, verify_release
    validate_profile(version, profile, previous_version)
    _validate_profile_policy(profile, update_channel, data_policy)
    build_id = str(uuid4())
    distribution_value = portable_distribution(version, build_id, update_channel=update_channel,
        data_policy=data_policy or "userdata", data_root_key=data_root_key)
    app_dir = plain_directory_root(app_dir)
    parent = plain_directory_root(output.parent)
    output = output.absolute()
    if output.exists() or output.is_symlink():
        raise ValueError("输出 ZIP 已存在；保留旧包，请使用新的产物路径")
    if output.is_relative_to(app_dir):
        raise ValueError("ZIP 输出必须位于目录构建之外")
    receipt = verify_onedir_receipt(app_dir, receipt_path, version=version)
    validate_app_bundle(app_dir / APP_EXE, app_dir / "_internal", source_manifest_path=source_component_manifest(source_root))
    # 只有一份必要 staging；源 onedir 不写发行标识，仍可交同版本安装器消费。
    with tempfile.TemporaryDirectory(prefix=".portable-stage-", dir=parent) as temporary:
        work = plain_directory_root(Path(temporary))
        assert work.is_relative_to(parent) and work != parent
        stage = work / ARCHIVE_ROOT
        stage.mkdir()
        files = []
        for row in receipt["files"]:
            source = app_dir / row["path"]
            if not stat.S_ISREG(plain_member_stat(app_dir, source).st_mode):
                raise ValueError("构建成员类型发生变化")
            target = stage / row["path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            if target.stat().st_size != row["size"] or sha256(target) != row["sha256"]:
                raise ValueError("暂存期间构建输入发生变化")
            files.append({**row, "bootstrap": bootstrap_path(row["path"])})
        distribution_bytes = _metadata_bytes(distribution_value)
        (stage / "distribution.json").write_bytes(distribution_bytes)
        files.append({"path": "distribution.json", "size": len(distribution_bytes),
                      "sha256": hashlib.sha256(distribution_bytes).hexdigest(), "bootstrap": False})
        manifest = parse_release_manifest({"schema_version": 1, "product": "nte-drive-calc", "version": version,
            "build_id": build_id, "architecture": "x64", "files": sorted(files, key=lambda row: row["path"])})
        (stage / "release-files.json").write_bytes(_metadata_bytes(manifest.to_dict()))
        verify_release(stage, load_distribution(stage))
        staged_zip = work / "candidate.zip"
        with zipfile.ZipFile(staged_zip, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True) as archive:
            for relative in sorted([row["path"] for row in files] + ["release-files.json"]):
                archive.write(stage / relative, f"{ARCHIVE_ROOT}/{relative}")
        limit = KOKODAYO_MAX_BYTES if profile == "kokodayo" else 4 * 1024**3
        summary = verify_portable_archive(staged_zip, max_compressed_bytes=limit, profile=profile)
        # 同目录发布，旧文件从不覆盖；最后重检成员身份，避免把路径别名当普通输出。
        plain_directory_root(parent)
        if output.exists() or output.is_symlink():
            raise ValueError("发布前 ZIP 目标已被占用")
        staged_zip.rename(output)
    return {**summary, "path": str(output), "source_build_id": receipt["build_id"], "profile": profile,
            "previous_platform_version": previous_version,
            "platform_install_directory_policy": "delete_then_extract_static_client_0.1.7" if profile == "kokodayo" else "unconfirmed",
            "platform_update_exit_and_external_data_preservation": "not_runtime_verified",
            "authenticity": "Hashes establish consistency; trusted distribution channel is still required."}
