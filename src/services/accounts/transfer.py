# 冻结正式账号身份，编排账号包暂存、校验与带补偿的目录/索引发布。
from __future__ import annotations

import json
import re
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from src.domain.post_actions import merge_post_action_config
from src.services.accounts.settings import AccountSettingsService, SETTING_GROUPS
from src.services.identification.matching_settings import MatchingSettingsService
from src.integrations.accounts.index_file import locked_account_index
from src.integrations.accounts.mutation_directories import (
    preserve_deleted_account_cleanup, require_account_directory_ready, retired_account_directory,
)
from src.integrations.accounts.transfer_archive import (
    CONFIG_FILENAMES, IMAGE_FILENAMES, MAX_JSON_BYTES, USER_DATABASE_FILENAME,
    account_directory_identity, export_account_archive, install_account_directory,
    stage_account_archive,
)
from src.storage.sqlite.accounts.transfer_database import (
    backup_account_database, portable_settings, prepare_account_database,
)


class AccountTransferStore(Protocol):
    accounts_dir: Path
    def read_index(self) -> dict: ...
    def write_index(self, data: dict) -> None: ...
    def account_dir(self, account_id: str) -> Path: ...


def _account_path(manager: AccountTransferStore, account_id: str) -> Path:
    if not isinstance(account_id, str) or re.fullmatch(r"[A-Za-z0-9_-]{1,80}", account_id) is None:
        raise ValueError("invalid_account_id")
    target = manager.account_dir(account_id)
    if target.resolve() != manager.accounts_dir.resolve() / account_id or target.is_symlink():
        raise ValueError("invalid_account_directory")
    return target


def _portable_config(source: Path, target: Path) -> None:
    if source.stat().st_size > MAX_JSON_BYTES:
        raise ValueError("账号配置文件大小超限")
    value = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("账号配置不是对象")
    group = {"hotkeys.json": "hotkeys", "update_config.json": "update", "ui_preferences.json": "ui"}.get(source.name)
    if group:
        value = portable_settings(group, value)
    elif source.name == "scan_post_actions.json":
        # 未打开扫描页的旧账号尚未懒迁移；只保留正式计划偏好，不携带任意配置。
        value = merge_post_action_config(value)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def _prepare_settings(staged: Path) -> None:
    # schema/JSON 合法不代表业务设置可用。复用运行时的迁移和规范化入口，
    # 在候选目录内读完全部分组；失败时旧 owner/索引/数据库均尚未触碰。
    settings = AccountSettingsService(staged / USER_DATABASE_FILENAME,
                                      legacy_config_dir=staged / "config")
    settings.migrate_legacy_settings()
    # 额外套装是账号业务偏好；暂存区校验失败时不切换当前账号。
    MatchingSettingsService(staged / USER_DATABASE_FILENAME).load()
    for key in SETTING_GROUPS:
        value = settings.load(key)
        if key == "ui" and value.get("full_scan_post_action_config"):
            merge_post_action_config(value["full_scan_post_action_config"])


def export_account_data(manager: AccountTransferStore, account_id: str, target_zip: Path) -> Path:
    """只读冻结账号的一致副本；未知 ID 不回退到第一个账号。"""
    with locked_account_index():
        account = next((dict(row) for row in manager.read_index().get("accounts", [])
                        if row.get("id") == account_id), None)
        if account is None:
            raise ValueError("account_not_found")
        source_root = _account_path(manager, account_id)
        require_account_directory_ready(source_root)
        identity = account_directory_identity(source_root)
    target_zip = Path(target_zip).resolve()
    if target_zip.is_relative_to(manager.accounts_dir.resolve()) or target_zip.is_relative_to(
        manager.accounts_dir.parent.resolve() / "config"
    ):
        raise ValueError("账号导出位置不得覆盖账号或本机配置目录")
    with tempfile.TemporaryDirectory(prefix=".account-export-", dir=manager.accounts_dir) as temporary:
        staged = Path(temporary)
        database = source_root / USER_DATABASE_FILENAME
        if database.resolve().parent != source_root.resolve() or database.is_symlink():
            raise ValueError("invalid_account_database")
        backup_account_database(database, staged / USER_DATABASE_FILENAME)
        prepare_account_database(staged / USER_DATABASE_FILENAME,
                                 account_id=account_id, account_name=account["name"])
        files = {USER_DATABASE_FILENAME: staged / USER_DATABASE_FILENAME}
        for relative in [*(f"config/{name}" for name in CONFIG_FILENAMES),
                         *(f"scanned_images/{name}" for name in IMAGE_FILENAMES)]:
            source = source_root / relative
            if not source.is_file():
                continue
            if source.is_symlink() or not source.resolve().is_relative_to(source_root.resolve()):
                raise ValueError("账号导出文件指向其他目录")
            if relative.startswith("config/"):
                _portable_config(source, staged / relative)
                files[relative] = staged / relative
            else:
                files[relative] = source
        _prepare_settings(staged)
        # 发布前复核元数据与目录身份；不持索引锁做备份/压缩，不冻结其他账号更新。
        with locked_account_index():
            current = next((row for row in manager.read_index().get("accounts", [])
                            if row.get("id") == account_id), None)
            if current != account or account_directory_identity(source_root) != identity:
                raise ValueError("account_export_target_changed")
        return export_account_archive(target_zip, account, files)


def import_account_data(manager: AccountTransferStore, source_zip: Path,
                        before_replace: Callable[[str], None] | None = None) -> str:
    """同正式 ID 替换；同名不同 ID 保留，显示名不参与覆盖决策。"""
    with stage_account_archive(Path(source_zip), manager.accounts_dir) as (account, staged):
        target_id = account.get("id")
        target = _account_path(manager, target_id)
        name = account.get("name")
        if not isinstance(name, str) or not name.strip() or len(name) > 256:
            raise ValueError("账号导入文件缺少有效账号名称")
        with locked_account_index():
            existing = next((dict(row) for row in manager.read_index().get("accounts", [])
                             if row.get("id") == target_id), None)
            if existing is not None:
                # 索引仍登记时恢复位保有唯一原件；候选不得覆盖它或先释放当前 owner。
                require_account_directory_ready(target)
            identity = account_directory_identity(target)
            retired_identity = account_directory_identity(retired_account_directory(target))
            if existing is None and identity is not None:
                raise ValueError("账号目录已存在但未登记，导入未覆盖")
        prepare_account_database(staged / USER_DATABASE_FILENAME,
                                 account_id=target_id, account_name=name)
        for filename in CONFIG_FILENAMES:
            config = staged / "config" / filename
            if config.is_file():
                _portable_config(config, config)
        _prepare_settings(staged)
        for subdir in ("config", "scanned_images", "logs"):
            (staged / subdir).mkdir(exist_ok=True)
        # 数据准备失败不影响运行时；停止/切换回调始终位于索引锁外。
        if existing is not None and before_replace is not None:
            before_replace(target_id)
        with locked_account_index():
            current = manager.read_index()
            rows = current.get("accounts", [])
            current_target = next((row for row in rows if row.get("id") == target_id), None)
            if current_target != existing or account_directory_identity(target) != identity:
                raise ValueError("account_import_target_changed")
            if account_directory_identity(retired_account_directory(target)) != retired_identity:
                raise ValueError("account_import_target_changed")
            if existing is None:
                # 索引删除已提交的旧残留只保留物理文件，不阻止同 ID 的完整新候选就绪。
                preserve_deleted_account_cleanup(target, retired_identity)
            else:
                require_account_directory_ready(target)
            replacement = dict(current)
            replacement["accounts"] = [({"id": target_id, "name": name}
                                        if row.get("id") == target_id else dict(row)) for row in rows]
            if existing is None:
                replacement["accounts"].append({"id": target_id, "name": name})
            replacement["active_account_id"] = target_id
            # 索引写者原子替换成功即完成；此前目录失败或索引写失败都恢复整份原件。
            install_account_directory(staged, target, lambda: manager.write_index(replacement))
        return target_id
