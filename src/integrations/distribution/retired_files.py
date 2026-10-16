# 仅按成功接受的旧发行清单清理精确非引导文件；未知文件保留并阻止接管。
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import tempfile

from .release_files import (
    ReleaseManifest, ReleaseValidationError, VerifiedRelease, checked_path, file_sha256,
    bootstrap_path, parse_release_manifest, read_json, recheck_verified,
)


ACCEPTED_MANIFEST = "accepted-release.json"


@dataclass(frozen=True, slots=True)
class RetirementResult:
    removed: tuple[str, ...]
    first_acceptance: bool
    retained: tuple[str, ...] = ()


def _accepted(maintenance_dir: Path) -> ReleaseManifest | None:
    path = checked_path(maintenance_dir, ACCEPTED_MANIFEST)
    if not path.exists():
        return None
    value = read_json(path)
    if (not isinstance(value, dict) or set(value) != {"schema_version", "manifest", "manifest_sha256"}
            or type(value["schema_version"]) is not int or value["schema_version"] != 1):
        raise ReleaseValidationError("已接受发行记录损坏；保留原文件，请进行前置维护。")
    manifest = parse_release_manifest(value["manifest"])
    if hashlib.sha256(manifest.canonical_bytes()).hexdigest() != value["manifest_sha256"]:
        raise ReleaseValidationError("已接受发行记录摘要不一致；保留原文件，请进行前置维护。")
    return manifest


def _inventory(root: Path) -> set[str]:
    found = set()
    pending = [root]
    while pending:
        directory = pending.pop()
        for item in directory.iterdir():
            relative = item.relative_to(root).as_posix()
            identity = relative.casefold()
            if directory == root and identity == "unins000.exe":
                # Inno 默认卸载入口不在本应用组件发现链中；保留，不背书其内容或登记。
                checked_path(root, relative)
                if not item.is_file():
                    raise ReleaseValidationError("保留的卸载入口不是普通文件。")
                continue
            # 用户根不属于发行文件域，不枚举、更不以包内路径删除它。
            if identity == "userdata":
                continue
            if directory == root and identity not in {"_internal", "distribution.json", "release-files.json"} and not bootstrap_path(relative):
                # 平台说明、快捷方式和用户目录不由我们拥有，也不遍历其内部。
                continue
            checked_path(root, relative)
            if item.is_dir():
                pending.append(item)
            else:
                found.add(relative)
    return found


def reconcile_release(app_dir: Path, maintenance_dir: Path, verified: VerifiedRelease | None) -> RetirementResult:
    """须持有数据根互斥；先检查全部路径再删除，锁不约束不合作的旧程序。"""
    if verified is None:
        return RetirementResult((), False)
    if Path(os.path.abspath(app_dir)) != verified.app_dir:
        raise ReleaseValidationError("发行校验对象改变。")
    try:
        recheck_verified(verified)
        old = _accepted(Path(maintenance_dir))
        if old and old.build_id == verified.manifest.build_id and old != verified.manifest:
            raise ReleaseValidationError("同一 build_id 对应不同文件；重新打包需要新的构建身份。")
        # Windows 覆盖保留旧物理大小写；清单身份按 casefold，删除仍用实际盘面路径。
        current_paths = {row.path.casefold() for row in verified.manifest.files}
        retired = {row.path.casefold(): row for row in old.files if row.path.casefold() not in current_paths} if old else {}
        inventory = _inventory(verified.app_dir)
        disk_paths = {path.casefold(): path for path in inventory}
        if len(disk_paths) != len(inventory):
            raise ReleaseValidationError("程序目录存在 Windows 大小写冲突，请先进行前置维护。")
        extras = set(disk_paths) - current_paths - {"release-files.json"}
        unknown = extras - set(retired)
        dangerous = {p for p in unknown if p.startswith("_internal/") or bootstrap_path(p)}
        if dangerous:
            raise ReleaseValidationError(f"未受管加载文件已保留：{sorted(dangerous)[0]}；请退出并由平台或人工前置维护，不自动接管旧加载链。")
        retained = set(unknown)
        planned = []
        for identity in sorted(extras & set(retired)):
            relative = disk_paths[identity]
            row = retired[identity]
            if row.bootstrap:
                raise ReleaseValidationError(f"退役引导文件需启动前清理：{relative}；当前程序不承诺修复已加载或损坏的加载链。")
            path = checked_path(verified.app_dir, relative)
            if not path.is_file() or path.stat().st_size != row.size or file_sha256(path) != row.sha256:
                raise ReleaseValidationError(f"退役文件已修改，保留并停止启动：{relative}")
            planned.append((relative, row))
        removed = []
        for relative, row in planned:
            path = checked_path(verified.app_dir, relative)
            if path.stat().st_size != row.size or file_sha256(path) != row.sha256:
                raise ReleaseValidationError(f"退役文件检查后改变，保留并停止启动：{relative}")
            path.unlink()
            removed.append(relative)
        return RetirementResult(tuple(removed), old is None, tuple(sorted(retained)))
    except (OSError, ValueError, TypeError) as error:
        raise ReleaseValidationError("发行维护失败，未处理文件保持原位；请退出占用程序后重试。") from error


def accept_release(maintenance_dir: Path, verified: VerifiedRelease | None) -> None:
    """仅由可见启动成功的 owner 调用；失败构建不成为下一次清理的所有权依据。"""
    if verified is None:
        return
    recheck_verified(verified)
    root = Path(maintenance_dir)
    checked_path(root, ACCEPTED_MANIFEST)
    root.mkdir(parents=True, exist_ok=True)
    value = {"schema_version": 1, "manifest": verified.manifest.to_dict(), "manifest_sha256": verified.manifest_sha256}
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=root, prefix=".accepted-", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(value, stream, ensure_ascii=False, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, root / ACCEPTED_MANIFEST)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
