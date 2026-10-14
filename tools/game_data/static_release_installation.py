# 成对安装主静态库与清单，并在恢复受阻时保留完整旧版材料。
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import shutil
import tempfile
from typing import Iterator

try:
    from .static_release_size_policy import StaticReleasePromotionError
except ImportError:  # 支持正式晋升脚本直接运行。
    from static_release_size_policy import StaticReleasePromotionError


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest().upper()


def _temporary_path(target: Path, label: str, owned: list[Path]) -> Path:
    handle, name = tempfile.mkstemp(
        prefix=f".{target.name}.{label}.", suffix=".tmp", dir=target.parent,
    )
    path = Path(name)
    owned.append(path)
    os.close(handle)
    return path


@contextmanager
def install_static_release_pair(
    source_database: Path,
    source_manifest: Path,
    target_database: Path,
    target_manifest: Path,
    expected_database_hash: str,
) -> Iterator[None]:
    """后验校验也属于安装事务；只在整体成功或完整恢复后清理旧版。"""
    targets = (target_database, target_manifest)
    temporary: list[Path] = []
    pending: dict[Path, Path] = {}
    backups: dict[Path, Path] = {}
    baseline_hashes: dict[Path, str] = {}
    installed: list[Path] = []
    recovery_errors: list[str] = []
    failure: BaseException | None = None
    try:
        for source, target in zip((source_database, source_manifest), targets):
            pending[target] = _temporary_path(target, "pending", temporary)
            shutil.copy2(source, pending[target])
        if sha256(pending[target_database]) != expected_database_hash:
            raise StaticReleasePromotionError("候选数据库复制到 pending 后 SHA-256 变化")

        # 两份旧文件都复制并核对后才开始替换，未存在的成员仍保持缺失身份。
        for target in targets:
            if target.is_file():
                baseline_hashes[target] = sha256(target)
                backups[target] = _temporary_path(target, "rollback", temporary)
                shutil.copy2(target, backups[target])
                if sha256(backups[target]) != baseline_hashes[target]:
                    raise StaticReleasePromotionError(f"旧版恢复副本 SHA-256 不一致：{target}")
        for target in targets:
            os.replace(pending[target], target)
            installed.append(target)
        yield
    except BaseException as exc:
        failure = exc
        # 每个已替换成员独立收尾；一个恢复失败不得阻止另一个恢复。
        for target in installed:
            try:
                if target in backups:
                    # 恢复时消费临时副本而非备份本体，部分恢复后仍保留整套旧版。
                    restore = _temporary_path(target, "restore", temporary)
                    shutil.copy2(backups[target], restore)
                    if sha256(restore) != baseline_hashes[target]:
                        raise StaticReleasePromotionError(f"恢复暂存 SHA-256 不一致：{target}")
                    os.replace(restore, target)
                else:
                    target.unlink(missing_ok=True)
            except BaseException as restore_error:
                recovery_errors.append(f"{target}：{type(restore_error).__name__}: {restore_error}")
        if recovery_errors:
            exc.add_note("静态晋升回滚未完成；先核对以下恢复材料，处理完成前请勿重试晋升。")
            for error in recovery_errors:
                exc.add_note(f"恢复失败：{error}")
            for target in targets:
                if target in backups:
                    exc.add_note(f"旧版恢复文件：{backups[target]} -> {target}；SHA-256={baseline_hashes[target]}")
                else:
                    exc.add_note(f"旧版成员原本不存在，恢复目标为保持缺失：{target}")
        raise
    finally:
        # 异常清理自身也逐项尝试并保留原始异常；绝不删除未完成恢复的备份。
        cleanup_errors: list[str] = []
        for path in temporary:
            if recovery_errors and path in backups.values():
                continue
            try:
                path.unlink(missing_ok=True)
            except OSError as cleanup_error:
                cleanup_errors.append(f"{path}：{cleanup_error}")
        if cleanup_errors:
            detail = "静态晋升临时文件清理失败：\n" + "\n".join(cleanup_errors)
            if failure is not None:
                failure.add_note(detail)
            else:
                raise StaticReleasePromotionError("新版本已安装并通过校验；" + detail)
