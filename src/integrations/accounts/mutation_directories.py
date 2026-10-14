# 在账号目录内暂存新建与隔离删除，区分索引提交和提交后的残留文件清理。
from __future__ import annotations

import re
import shutil
import tempfile
from collections.abc import Callable
from pathlib import Path
from uuid import uuid4

from src.integrations.accounts.transfer_archive import account_directory_identity
from src.utils.logger import logger


class AccountMutationRecoveryError(RuntimeError):
    """恢复目录仍保有原件；调用方不得把缺失的正式路径重新初始化为空账号。"""


def account_mutation_path(accounts_dir: Path, account_id: str) -> Path:
    root = accounts_dir.resolve()
    if not isinstance(account_id, str) or re.fullmatch(r"[A-Za-z0-9_-]{1,80}", account_id) is None:
        raise ValueError("invalid_account_id")
    target = root / account_id
    if target.is_symlink() or target.resolve() != target:
        raise ValueError("invalid_account_directory")
    return target


def retired_account_directory(target: Path) -> Path:
    # 固定正式 ID 恢复位只用于本次删除；不扫描或自动删除其他账号的残留目录。
    return target.parent / f".account-deleted-{target.name}"


def directory_occupied(path: Path) -> bool:
    return path.exists() or path.is_symlink()


def require_account_directory_ready(target: Path) -> None:
    retired = retired_account_directory(target)
    if directory_occupied(retired):
        raise AccountMutationRecoveryError(f"账号目录恢复或清理待完成，请保留并核对：{retired}")


def preserve_deleted_account_cleanup(target: Path, identity: tuple[int, int] | None) -> Path | None:
    """调用方持索引锁并已确认 ID 未登记；残留不再拥有新账号的恢复权限。"""
    retired = retired_account_directory(target)
    if identity is None:
        require_account_directory_ready(target)
        return None
    _require_directory_identity(retired, identity)
    # 只重命名此次确认的单个原件；不清理、不扫描同前缀目录，也不把旧残留当新账号事实。
    cleanup = target.parent / f".account-cleanup-{target.name}-{uuid4().hex}"
    if directory_occupied(cleanup):
        raise AccountMutationRecoveryError(f"账号清理保留位已被占用：{cleanup}")
    retired.rename(cleanup)
    logger.warning(f"已删除账号的残留原件已保留，物理清理仍待完成：{cleanup}")
    return cleanup


def _require_directory_identity(directory: Path, identity: tuple[int, int] | None) -> None:
    if directory.is_symlink() or account_directory_identity(directory) != identity:
        raise AccountMutationRecoveryError(f"账号目录身份已变化，未覆盖或清理：{directory}")


def _remove_owned_directory(directory: Path, accounts_dir: Path, identity: tuple[int, int] | None) -> None:
    # 每次递归清理前核对绝对父目录和链接身份；只操作本函数获得的单个隔离目录。
    if directory.is_symlink() or directory.resolve().parent != accounts_dir.resolve():
        raise ValueError("invalid_account_cleanup_directory")
    _require_directory_identity(directory, identity)
    shutil.rmtree(directory)


def create_account_directory(target: Path, prepare: Callable[[Path], None], publish: Callable[[], None]) -> None:
    if directory_occupied(target):
        raise ValueError("account_directory_already_exists")
    require_account_directory_ready(target)
    staged = Path(tempfile.mkdtemp(prefix=".account-create-", dir=target.parent))
    identity = account_directory_identity(staged)
    try:
        prepare(staged)
        # 只有完整关闭的 DAO 副本进入正式命名空间；不复用未登记目录中的旧用户库。
        if directory_occupied(target):
            raise ValueError("account_directory_already_exists")
        _require_directory_identity(staged, identity)
        staged.rename(target)
        try:
            publish()
        except BaseException:
            try:
                _require_directory_identity(target, identity)
                if directory_occupied(staged):
                    raise AccountMutationRecoveryError(f"新账号恢复位已被占用：{staged}")
                target.rename(staged)
            except OSError as error:
                raise AccountMutationRecoveryError(f"新账号索引未发布，目录保留待核对：{target}") from error
            raise
    finally:
        if directory_occupied(staged):
            try:
                _remove_owned_directory(staged, target.parent, identity)
            except (OSError, ValueError, AccountMutationRecoveryError):
                # 创建未发布的残留没有索引引用；保留现场，不覆盖原始失败原因。
                logger.warning(f"新账号暂存目录清理待完成：{staged}")


def delete_account_directory(target: Path, publish: Callable[[], None]) -> Path | None:
    """提交前不破坏原件；提交后清理故障只返回残留状态，不伪装成账号回滚。"""
    require_account_directory_ready(target)
    if not directory_occupied(target):
        publish()
        return None
    retired = retired_account_directory(target)
    identity = account_directory_identity(target)
    target.rename(retired)
    try:
        publish()
    except BaseException:
        try:
            _require_directory_identity(retired, identity)
            if directory_occupied(target):
                raise AccountMutationRecoveryError(f"账号恢复目标已被占用，原件保留于：{retired}")
            retired.rename(target)
        except OSError as error:
            raise AccountMutationRecoveryError(f"账号索引未更改，原目录恢复待完成，请保留：{retired}") from error
        raise
    try:
        _remove_owned_directory(retired, target.parent, identity)
    except (OSError, ValueError, AccountMutationRecoveryError):
        return retired
    return None
