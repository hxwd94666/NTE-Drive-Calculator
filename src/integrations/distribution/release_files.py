# 校验发行文件清单和完整包身份；摘要证明一致性，不替代分发渠道信任。
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat

from .metadata import Distribution, DistributionError, parse_distribution


class ReleaseValidationError(DistributionError):
    """包不完整或加载链需要在启动前修复。"""


BOOTSTRAP_SUFFIXES = frozenset({".exe", ".dll", ".pyd", ".pyz", ".pyc", ".pyo", ".py", ".zip", ".pth"})


def bootstrap_path(path: str) -> bool:
    return PurePosixPath(path).suffix.lower() in BOOTSTRAP_SUFFIXES


def release_path(value: object) -> str:
    if not isinstance(value, str) or not value or "\\" in value or ":" in value:
        raise ReleaseValidationError("发行清单路径格式错误。")
    parts = value.split("/")
    reserved = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
    if any(not p or p in {".", ".."} or p.endswith((".", " "))
           or any(ord(c) < 32 or c in '<>"|?*' for c in p)
           or p.split(".")[0].upper() in reserved for p in parts):
        raise ReleaseValidationError("发行清单路径越界或存在 Windows 路径别名。")
    if value not in {"NTE_Drive_Calc.exe", "distribution.json"} and not (
            len(parts) > 1 and parts[0] == "_internal"):
        raise ReleaseValidationError(f"发行清单包含非程序所有路径：{value}")
    return value


@dataclass(frozen=True, slots=True)
class ReleaseFile:
    path: str
    size: int
    sha256: str
    bootstrap: bool


@dataclass(frozen=True, slots=True)
class ReleaseManifest:
    schema_version: int
    product: str
    build_id: str
    version: str
    architecture: str
    files: tuple[ReleaseFile, ...]

    def to_dict(self) -> dict:
        value = asdict(self)
        value["files"] = [asdict(row) for row in self.files]
        return value

    def canonical_bytes(self) -> bytes:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


@dataclass(frozen=True, slots=True)
class VerifiedRelease:
    app_dir: Path
    distribution: Distribution
    manifest: ReleaseManifest
    manifest_sha256: str
    fingerprints: tuple[tuple[str, tuple[int, ...]], ...]


def _fingerprint(path: Path) -> tuple[int, ...]:
    info = path.stat()
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def recheck_verified(verified: VerifiedRelease) -> None:
    """同一次启动复核冻结文件身份，避免在锁/可见边界重复读取整包数百 MB。"""
    try:
        for relative, identity in verified.fingerprints:
            if _fingerprint(checked_path(verified.app_dir, relative)) != identity:
                raise ReleaseValidationError(f"整包校验后程序文件改变：{relative}")
    except OSError as error:
        raise ReleaseValidationError("整包校验后程序文件缺失或被占用。") from error


def parse_release_manifest(value: object) -> ReleaseManifest:
    keys = {"schema_version", "product", "build_id", "version", "architecture", "files"}
    if not isinstance(value, dict) or set(value) != keys:
        raise ReleaseValidationError("发行清单字段缺失或不受支持。")
    if (type(value["schema_version"]) is not int or value["schema_version"] != 1
            or value["product"] != "nte-drive-calc" or value["architecture"] != "x64"
            or any(not isinstance(value[k], str) or not value[k] for k in ("build_id", "version"))
            or not isinstance(value["files"], list) or not value["files"]):
        raise ReleaseValidationError("发行清单身份无效。")
    rows, seen, ancestors = [], set(), set()
    spelling = {}
    for row in value["files"]:
        if not isinstance(row, dict) or set(row) != {"path", "size", "sha256", "bootstrap"}:
            raise ReleaseValidationError("发行文件字段无效。")
        path = release_path(row["path"])
        key = path.casefold()
        parent_keys = {str(p).casefold() for p in PurePosixPath(path).parents if str(p) != "."}
        for component in (PurePosixPath(path), *PurePosixPath(path).parents):
            name = str(component)
            previous = spelling.setdefault(name.casefold(), name)
            if previous != name:
                raise ReleaseValidationError("发行目录路径存在大小写别名。")
        if key in seen or key in ancestors or parent_keys & seen:
            raise ReleaseValidationError("发行文件路径大小写冲突、重复或父子重叠。")
        seen.add(key)
        ancestors.update(parent_keys)
        if (type(row["size"]) is not int or row["size"] < 0
                or not isinstance(row["sha256"], str) or not re.fullmatch("[0-9a-f]{64}", row["sha256"])
                or type(row["bootstrap"]) is not bool or row["bootstrap"] != bootstrap_path(path)):
            raise ReleaseValidationError(f"发行文件大小、摘要或引导分类无效：{path}")
        rows.append(ReleaseFile(**row))
    if not {"nte_drive_calc.exe", "distribution.json"} <= seen:
        raise ReleaseValidationError("发行清单缺少入口或发行标识。")
    return ReleaseManifest(**dict(value, files=tuple(sorted(rows, key=lambda r: r.path))))


def checked_path(root: Path, relative: str) -> Path:
    """逐级 lstat 拒绝链接/junction；不 resolve 后误把外部目标当本包文件。"""
    root = Path(os.path.abspath(root))
    current = Path(root.anchor)
    for part in (*root.parts[1:], *PurePosixPath(relative).parts):
        if part:
            current /= part
        try:
            info = current.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            raise ReleaseValidationError(f"程序路径含链接或重解析点：{current}")
    return current


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> object:
    def unique_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result
    if path.stat().st_size > 32 * 1024 * 1024:
        raise ReleaseValidationError("发行清单超过大小限制。")
    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique_pairs)


def verify_release(app_dir: Path, distribution: Distribution) -> VerifiedRelease | None:
    """在数据根选择、迁移和业务副作用前只读核验整包；不尝试修复启动加载器。"""
    root = Path(os.path.abspath(app_dir))
    try:
        manifest_path = checked_path(root, distribution.release_manifest)
        if distribution.build_id == "legacy":
            if manifest_path.exists():
                raise ReleaseValidationError("发行标识丢失但清单仍存在，请从可信渠道修复完整程序包。")
            return None
        manifest_identity = _fingerprint(manifest_path)
        manifest = parse_release_manifest(read_json(manifest_path))
        for field in ("product", "build_id", "version", "architecture"):
            if getattr(manifest, field) != getattr(distribution, field):
                raise ReleaseValidationError(f"发行标识与文件清单不一致：{field}")
        fingerprints = [(distribution.release_manifest, manifest_identity)]
        for entry in manifest.files:
            path = checked_path(root, entry.path)
            identity = _fingerprint(path)
            if not path.is_file() or path.stat().st_size != entry.size or file_sha256(path) != entry.sha256:
                raise ReleaseValidationError(f"程序文件缺失或摘要不匹配：{entry.path}；请退出后重新覆盖完整程序包。")
            fingerprints.append((entry.path, identity))
        if parse_distribution(read_json(root / "distribution.json")) != distribution:
            raise ReleaseValidationError("读取发行身份后文件发生改变。")
        result = VerifiedRelease(root, distribution, manifest, hashlib.sha256(manifest.canonical_bytes()).hexdigest(), tuple(fingerprints))
        recheck_verified(result)
        return result
    except (OSError, ValueError, TypeError) as error:
        raise ReleaseValidationError("完整程序包校验失败；请从可信渠道重新解压或使用平台修复。") from error
