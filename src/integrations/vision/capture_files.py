# 管理全量截图独立暂存、目录租约和提交后的精确发布与恢复。
from __future__ import annotations

import json
import hashlib
import os
import re
import shutil
import stat
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator


class CaptureFileError(RuntimeError):
    pass


@dataclass(frozen=True)
class CaptureFile:
    name: str
    version: tuple[int, int, int, int]


@dataclass(frozen=True)
class CaptureStage:
    root: Path
    directory: Path
    capture_id: str
    files: tuple[CaptureFile, ...]
    root_identity: tuple[int, int]
    directory_identity: tuple[int, int]
    marker_version: tuple[int, int, int, int]
    image_sha256: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class CapturePublication:
    stage: CaptureStage
    previous: tuple[CaptureFile, ...]
    backup: Path


@dataclass(frozen=True)
class CapturePublicationResult:
    status: str
    cleanup_pending: bool = False
    message: str = ""
    remaining_moves: tuple[tuple[str, str], ...] = ()


def _ordinary(path: Path, directory: bool = False) -> os.stat_result:
    value = path.lstat()
    if stat.S_ISLNK(value.st_mode) or getattr(value, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
        raise CaptureFileError("截图路径含链接或重解析点。")
    if not (stat.S_ISDIR(value.st_mode) if directory else stat.S_ISREG(value.st_mode)):
        raise CaptureFileError("截图路径类型与所属阶段不符。")
    return value


def _root(path: str | Path) -> Path:
    root = Path(path).absolute()
    for ancestor in reversed([root, *root.parents]):
        _ordinary(ancestor, directory=True)
    return root


def _directory_identity(path: Path) -> tuple[int, int]:
    value = _ordinary(_root(path), directory=True)
    return value.st_dev, value.st_ino


def _check_directory(path: Path, identity: tuple[int, int]) -> None:
    if _directory_identity(path) != identity:
        raise CaptureFileError("截图所属目录在本次任务期间被替换。")


def _version(path: Path) -> tuple[int, int, int, int]:
    value = _ordinary(path)
    return value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns


def _image_sha256(path: Path, version: tuple[int, int, int, int]) -> str:
    if _version(path) != version:
        raise CaptureFileError("本次暂存截图在读取前发生变化。")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    if _version(path) != version:
        raise CaptureFileError("本次暂存截图在读取期间发生变化。")
    return digest.hexdigest()


def _formal_name(name: str) -> bool:
    match = re.fullmatch(r"raw_drive_([0-9]{4,})\.png", name)
    return bool(match and int(match[1]) > 0 and name == f"raw_drive_{int(match[1]):04d}.png")


@contextmanager
def capture_output_lease(path: str | Path) -> Iterator[None]:
    """保留锁文件，操作系统锁随句柄释放；取消请求不提前释放正在收尾的租约。"""
    root = _root(path)
    lock_path = root / ".nte-scan-output.lock"
    if lock_path.exists() or lock_path.is_symlink():
        _ordinary(lock_path)
    with lock_path.open("a+b") as handle:
        if not handle.seek(0, os.SEEK_END):
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        if _version(lock_path)[:2] != (os.fstat(handle.fileno()).st_dev, os.fstat(handle.fileno()).st_ino):
            raise CaptureFileError("截图租约文件在打开期间发生变化。")
        if os.name == "nt":
            import msvcrt
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as error:
                raise CaptureFileError("同一截图目录正在提交或整理，请等待任务结束。") from error
        else:
            import fcntl
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as error:
                raise CaptureFileError("同一截图目录正在提交或整理，请等待任务结束。") from error
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)


def create_capture_directory(path: str | Path) -> str:
    root = Path(path).absolute()
    for ancestor in reversed([root, *root.parents]):
        try:
            ancestor.lstat()
        except FileNotFoundError:
            ancestor.mkdir()
        _ordinary(ancestor, directory=True)
    with capture_output_lease(root):
        capture_id = uuid.uuid4().hex
        directory = root / (".capture-" + capture_id)
        directory.mkdir()
        (directory / ".nte-capture.json").write_text(json.dumps({"capture_id": capture_id}), encoding="utf-8")
        return str(directory)


def freeze_capture_stage(root: str | Path, directory: str | Path, expected_count: int) -> CaptureStage:
    root = _root(root)
    root_identity = _directory_identity(root)
    directory = Path(directory).absolute()
    if directory.parent != root or not re.fullmatch(r"\.capture-[a-f0-9]{32}", directory.name):
        raise CaptureFileError("截图暂存不属于本次扫描目录。")
    directory_identity = _directory_identity(directory)
    marker = directory / ".nte-capture.json"
    marker_version = _version(marker)
    if marker.stat().st_size > 512:
        raise CaptureFileError("截图暂存身份格式异常。")
    capture_id = directory.name.removeprefix(".capture-")
    if json.loads(marker.read_text(encoding="utf-8")) != {"capture_id": capture_id}:
        raise CaptureFileError("截图暂存身份不一致。")
    metadata_names = {marker.name, ".nte-publication.json", ".nte-publication.tmp"}
    for metadata in directory.iterdir():
        if metadata.name in metadata_names:
            _ordinary(metadata)
    files = tuple(CaptureFile(p.name, _version(p)) for p in sorted(directory.iterdir()) if p.name not in metadata_names)
    if len(files) != expected_count or {f.name for f in files} != {f"raw_drive_{i:04d}.png" for i in range(1, expected_count + 1)}:
        raise CaptureFileError("本次暂存截图的数量或正式编号不完整。")
    _check_directory(root, root_identity)
    _check_directory(directory, directory_identity)
    if _version(marker) != marker_version:
        raise CaptureFileError("截图暂存身份在读取期间发生变化。")
    fingerprints = tuple((item.name, _image_sha256(directory / item.name, item.version)) for item in files)
    if _version(marker) != marker_version:
        raise CaptureFileError("截图暂存身份在读取期间发生变化。")
    _check_directory(root, root_identity)
    _check_directory(directory, directory_identity)
    return CaptureStage(root, directory, capture_id, files, root_identity, directory_identity, marker_version, fingerprints)


def check_capture_stage(stage: CaptureStage) -> None:
    """先用便宜身份拒绝旧输入，必要边界再逐块核对本次完整图片字节。"""
    if not isinstance(stage, CaptureStage) or not stage.files or not stage.image_sha256:
        raise CaptureFileError("本次暂存缺少完整截图字节身份。")
    _check_directory(stage.root, stage.root_identity)
    _check_directory(stage.directory, stage.directory_identity)
    if _version(stage.directory / ".nte-capture.json") != stage.marker_version:
        raise CaptureFileError("本次暂存身份已发生变化。")
    if any(_version(stage.directory / item.name) != item.version for item in stage.files):
        raise CaptureFileError("本次暂存截图已发生变化。")
    if freeze_capture_stage(stage.root, stage.directory, len(stage.files)) != stage:
        raise CaptureFileError("本次暂存截图已发生变化。")


def prepare_capture_publication(stage: CaptureStage) -> CapturePublication:
    check_capture_stage(stage)
    previous = tuple(CaptureFile(p.name, _version(p)) for p in sorted(stage.root.iterdir()) if _formal_name(p.name))
    previous_names = {f.name for f in previous}
    for item in stage.files:
        target = stage.root / item.name
        if (target.exists() or target.is_symlink()) and item.name not in previous_names:
            raise CaptureFileError("新截图与非本批正式文件冲突。")
    backup = stage.root / (".previous-" + stage.capture_id)
    if backup.exists() or backup.is_symlink():
        raise CaptureFileError("本次截图恢复目录已存在，请先核对恢复状态。")
    # 同卷只移动文件，不复制整套图片；仍为小型日志及目录元数据预留空间。
    if shutil.disk_usage(stage.root).free < 65536 + 1024 * (len(previous) + len(stage.files)):
        raise CaptureFileError("截图目录空间不足，尚未提交背包。")
    return CapturePublication(stage, previous, backup)


def publish_capture_files(plan: CapturePublication, snapshot_id: int | None = None,
                          cancel_check: Callable[[], bool] | None = None) -> CapturePublicationResult:
    stage = plan.stage
    moves: list[tuple[Path, Path, tuple[int, int, int, int]]] = []
    unresolved: list[tuple[str, str]] = []
    journal = stage.directory / ".nte-publication.json"
    backup_identity: tuple[int, int] | None = None
    journal_version: tuple[int, int, int, int] | None = None

    def check_locations() -> None:
        # 叶文件相同并不足以证明归属：正向、恢复、日志和清理都核对冻结父目录。
        _check_directory(stage.root, stage.root_identity)
        _check_directory(stage.directory, stage.directory_identity)
        if backup_identity is not None:
            _check_directory(plan.backup, backup_identity)

    def save(status: str) -> None:
        nonlocal journal_version
        check_locations()
        data = {"capture_id": stage.capture_id, "snapshot_id": snapshot_id, "status": status,
                "backup": plan.backup.name, "unrestored_moves": unresolved,
                "moves": [(str(a.relative_to(stage.root)), str(b.relative_to(stage.root)), v) for a, b, v in moves]}
        temp = journal.with_suffix(".tmp")
        for path in (temp, journal):
            if path.exists() or path.is_symlink():
                _ordinary(path)
        temp.write_text(json.dumps(data), encoding="utf-8")
        check_locations()
        os.replace(temp, journal)
        journal_version = _version(journal)

    def move(source: Path, target: Path, version: tuple[int, int, int, int]) -> None:
        check_locations()
        if cancel_check is not None and cancel_check():
            raise CaptureFileError("背包提交后截图整理已停止。")
        if _version(source) != version or target.exists() or target.is_symlink():
            raise CaptureFileError("截图移动前源或目标身份发生变化。")
        os.replace(source, target)
        moves.append((source, target, version))
        save("publishing")

    try:
        check_locations()
        if tuple(CaptureFile(p.name, _version(p)) for p in sorted(stage.root.iterdir()) if _formal_name(p.name)) != plan.previous:
            raise CaptureFileError("原截图在提交期间发生变化，保留本次暂存。")
        if cancel_check is not None and cancel_check():
            raise CaptureFileError("背包已保存，截图整理尚未开始。")
        plan.backup.mkdir()
        backup_identity = _directory_identity(plan.backup)
        save("publishing")
        for item in plan.previous:
            move(stage.root / item.name, plan.backup / item.name, item.version)
        for item in stage.files:
            move(stage.directory / item.name, stage.root / item.name, item.version)
        check_locations()
        for item in stage.files:
            if _version(stage.root / item.name) != item.version:
                raise CaptureFileError("发布后的截图身份发生变化。")
        save("published")
    except Exception as error:
        for source, target, version in reversed(moves):
            try:
                check_locations()
                if _version(target) != version or source.exists() or source.is_symlink():
                    raise CaptureFileError("恢复目标已改变。")
                os.replace(target, source)
            except (OSError, CaptureFileError):
                unresolved.append((str(source.relative_to(stage.root)), str(target.relative_to(stage.root))))
        status = "restore_incomplete" if unresolved else "pending"
        try:
            save(status)
        except (OSError, CaptureFileError):
            pass  # 日志故障不等于数据库回滚；结果仍携带确切未恢复步骤。
        if backup_identity is not None:
            try:
                check_locations()
                plan.backup.rmdir()  # 仅空目录；不删除未恢复成员或被替换目录。
            except (OSError, CaptureFileError):
                pass
        return CapturePublicationResult(status, message=str(error), remaining_moves=tuple(unresolved))

    pending = False
    for item in plan.previous:
        try:
            check_locations()
            path = plan.backup / item.name
            if _version(path) != item.version:
                raise CaptureFileError("备份成员已改变。")
            path.unlink()
        except (OSError, CaptureFileError):
            pending = True
    try:
        check_locations()
        plan.backup.rmdir()
        backup_identity = None  # 此后只核对仍存在的 root/stage。
    except (OSError, CaptureFileError):
        pending = True
    if not pending:
        try:
            check_locations()
            if _version(journal) != journal_version or _version(stage.directory / ".nte-capture.json") != stage.marker_version:
                raise CaptureFileError("本次恢复记录或暂存身份已改变。")
            journal.unlink()
            check_locations()
            (stage.directory / ".nte-capture.json").unlink()
            check_locations()
            stage.directory.rmdir()
        except (OSError, CaptureFileError):
            pending = True
    return CapturePublicationResult("published", cleanup_pending=pending)
