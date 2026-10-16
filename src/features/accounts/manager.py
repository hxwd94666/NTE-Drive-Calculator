# 管理账号创建、切换、导入和导出。
"""Account storage, migration, and account management dialog helpers."""

from __future__ import annotations

import json
import re
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from src.app.context import AccountContext
from src.integrations.accounts.index_file import (
    locked_account_index, read_account_index_bytes, write_account_index_bytes,
)
from src.integrations.distribution.lock import reject_links
from src.integrations.accounts.mutation_directories import (
    AccountMutationRecoveryError,
    account_mutation_path, create_account_directory, delete_account_directory,
    directory_occupied, require_account_directory_ready, retired_account_directory,
)
from src.services.accounts.naming import AccountNamingService, normalize_account_name
from src.services.accounts.transfer import export_account_data, import_account_data
from src.integrations.accounts.transfer_archive import AccountImportRecoveryError, account_directory_identity
from src.storage.config_migration import replace_core_config_dir
from src.storage.sqlite.user_data_dao import UserDataDao
from src.utils.logger import logger


USER_DATABASE_FILENAME = "user_data.sqlite3"


AccountState = AccountContext


@dataclass(frozen=True)
class AccountDeletionTarget:
    account_id: str
    account: dict
    directory_identity: tuple[int, int] | None


@dataclass(frozen=True)
class AccountDeletionResult:
    active_account_id: str
    cleanup_pending: Path | None


class AccountManager:
    """Manage multi-account metadata and per-account data folders."""

    def __init__(
        self,
        data_root: Path,
        bundled_config_dir: Path,
        iter_image_files,
        core_config_files: tuple[str, ...],
        account_user_files: tuple[str, ...],
    ):
        self.data_root = Path(data_root)
        self.bundled_config_dir = Path(bundled_config_dir)
        self.iter_image_files = iter_image_files
        self.core_config_files = tuple(core_config_files)
        self.account_user_files = tuple(account_user_files)

        self.config_dir = self.data_root / "config"
        self.accounts_dir = self.data_root / "accounts"
        self.accounts_index_file = self.accounts_dir / "accounts.json"
        self.template_dir = self.config_dir / "templates"

    def safe_account_id(self, name: str) -> str:
        cleaned = re.sub(r"[^A-Za-z0-9_-]+", "_", (name or "").strip()).strip("_")
        return cleaned[:40] or f"account_{int(time.time())}"

    def read_index(self) -> dict:
        with locked_account_index():
            if self.accounts_index_file.exists():
                try:
                    data = json.loads(read_account_index_bytes(self.accounts_index_file).decode("utf-8"))
                    if isinstance(data, dict) and data.get("accounts"):
                        return data
                except Exception as exc:
                    logger.warning(f"账号索引读取失败，使用默认账号配置: {exc}")
            return {"active_account_id": "default", "accounts": [{"id": "default", "name": "默认账号"}]}

    def write_index(self, data: dict) -> None:
        raw = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
        write_account_index_bytes(self.accounts_index_file, raw)

    def account_meta(self, account_id: str | None = None) -> dict:
        data = self.read_index()
        target = account_id or data.get("active_account_id") or "default"
        for account in data.get("accounts", []):
            if account.get("id") == target:
                return account
        return data.get("accounts", [{"id": "default", "name": "默认账号"}])[0]

    def account_dir(self, account_id: str) -> Path:
        return self.accounts_dir / account_id

    def seed_user_config(self) -> None:
        if not self.bundled_config_dir.exists():
            return
        replace_core_config_dir(
            self.config_dir, self.bundled_config_dir, self.core_config_files,
        )

        src_templates = self.bundled_config_dir / "templates"
        if src_templates.exists() and src_templates.resolve() != self.template_dir.resolve():
            # 模板在运行数据域可编辑；发行新增项只初始化缺项，不覆盖用户已有内容。
            for source in src_templates.rglob("*"):
                destination = self.template_dir / source.relative_to(src_templates)
                reject_links(destination)
                if source.is_file() and not destination.exists():
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, destination)

    def _seed_account_profile(self, account_id: str) -> Path:
        """调用方已持索引锁；正式 ID 重检后才取得账号 DAO 写锁。"""
        account = next((row for row in self.read_index().get("accounts", [])
                        if row.get("id") == account_id), None)
        if account is None:
            raise ValueError("account_not_found")
        account_root = account_mutation_path(self.accounts_dir, account_id)
        require_account_directory_ready(account_root)
        for subdir in (account_root / "config", account_root / "scanned_images", account_root / "logs"):
            subdir.mkdir(parents=True, exist_ok=True)
        account_name = str(account.get("name") or account_id)
        # 用户数据库只属账号运行数据；已有 profile 校正参与同一索引锁。
        with UserDataDao(account_root / USER_DATABASE_FILENAME,
                         account_id=account_id, account_name=account_name) as user_database:
            if user_database.profile()["account_name"] != account_name:
                user_database.rename_account(account_name)
        return account_root

    def _seed_legacy_account_files(self, account_root: Path, migrate_legacy: bool) -> None:
        # 模板与图片准备不持索引锁，不在临界区等待任务或触发账号切换。
        if not migrate_legacy:
            return
        account_config = account_root / "config"
        for fname in self.account_user_files:
            dst = account_config / fname
            src = self.data_root / "config" / fname
            if not dst.exists() and src.exists() and src.resolve() != dst.resolve():
                shutil.copy2(str(src), str(dst))
        legacy_screenshots = self.data_root / "scanned_images"
        account_screenshots = account_root / "scanned_images"
        if legacy_screenshots.exists() and not any(account_screenshots.iterdir()):
            for file in self.iter_image_files(legacy_screenshots):
                try:
                    shutil.copy2(str(file), str(account_screenshots / file.name))
                except Exception as exc:
                    logger.warning(f"迁移旧截图失败，已跳过该文件: {file} | {exc}")

    def seed_account_data(self, account_id: str, migrate_legacy: bool = False) -> None:
        with locked_account_index():
            account_root = self._seed_account_profile(account_id)
        self._seed_legacy_account_files(account_root, migrate_legacy)

    def activate(self, account_id: str) -> AccountState:
        with locked_account_index():
            data = self.read_index()
            account = next((a for a in data.get("accounts", []) if a.get("id") == account_id), None)
            if not account:
                account = {"id": "default", "name": "默认账号"}
            active_id = account["id"]
            account_root = self._seed_account_profile(active_id)
            return AccountState(
                active_account_id=active_id,
                active_account_name=account.get("name") or active_id,
                account_data_root=account_root,
                user_database_path=account_root / USER_DATABASE_FILENAME,
                user_config_dir=account_root / "config",
                screenshot_dir=account_root / "scanned_images",
                log_dir=account_root / "logs",
            )

    def initialize(self) -> AccountState:
        self.seed_user_config()
        with locked_account_index():
            data = self.read_index()
            ids = []
            normalized = []
            for account in data.get("accounts", []):
                account_id = self.safe_account_id(account.get("id") or account.get("name") or "default")
                if account_id in ids:
                    continue
                ids.append(account_id)
                normalized.append({"id": account_id, "name": account.get("name") or account_id})
            if not normalized:
                normalized = [{"id": "default", "name": "默认账号"}]
            active_id = data.get("active_account_id") if data.get("active_account_id") in ids else normalized[0]["id"]
            self.write_index({"active_account_id": active_id, "accounts": normalized})
            account_root = self._seed_account_profile(active_id)
        self._seed_legacy_account_files(account_root, active_id == "default")
        return self.activate(active_id)

    def set_active_account_id(self, account_id: str) -> None:
        with locked_account_index():
            data = self.read_index()
            if not any(a.get("id") == account_id for a in data.get("accounts", [])):
                raise ValueError("account_not_found")
            data["active_account_id"] = account_id
            self.write_index(data)

    def create_account(self, name: str) -> str:
        name = normalize_account_name(name)
        with locked_account_index():
            data = self.read_index()
            existing = {a.get("id") for a in data.get("accounts", [])}
            base = self.safe_account_id(name)
            account_id = base
            suffix = 2
            while (account_id in existing or directory_occupied(self.account_dir(account_id))
                   or directory_occupied(retired_account_directory(self.account_dir(account_id)))):
                account_id = f"{base}_{suffix}"
                suffix += 1
            target = account_mutation_path(self.accounts_dir, account_id)
            data.setdefault("accounts", []).append({"id": account_id, "name": name})

            def prepare(staged: Path) -> None:
                for subdir in ("config", "scanned_images", "logs"):
                    (staged / subdir).mkdir()
                with UserDataDao(staged / USER_DATABASE_FILENAME, account_id=account_id, account_name=name):
                    pass

            create_account_directory(target, prepare, lambda: self.write_index(data))
            return account_id

    def rename_account(self, account_id: str, name: str) -> None:
        database_path = self.account_dir(account_id) / USER_DATABASE_FILENAME
        if not database_path.is_file():
            raise ValueError("account_database_not_found")
        AccountNamingService(
            accounts_index_path=self.accounts_index_file,
            user_database_path=database_path,
            account_id=account_id,
            context_is_current=lambda: True,
        ).rename(name)

    def prepare_account_deletion(self, account_id: str) -> AccountDeletionTarget:
        with locked_account_index():
            accounts = self.read_index().get("accounts", [])
            account = next((row for row in accounts if row.get("id") == account_id), None)
            if account is None:
                raise ValueError("account_not_found")
            if not any(row.get("id") != account_id for row in accounts):
                raise ValueError("at_least_one_account_required")
            target = account_mutation_path(self.accounts_dir, account_id)
            require_account_directory_ready(target)
            return AccountDeletionTarget(account_id, dict(account), account_directory_identity(target))

    def delete_account(self, account_id: str, *, expected: AccountDeletionTarget | None = None,
                       active_account_id: str | None = None) -> AccountDeletionResult:
        with locked_account_index():
            current = self.prepare_account_deletion(account_id)
            if expected is not None and current != expected:
                raise ValueError("account_delete_target_changed")
            data = self.read_index()
            accounts = data.get("accounts", [])
            target = account_mutation_path(self.accounts_dir, account_id)
            was_active = data.get("active_account_id") == account_id
            data["accounts"] = [a for a in accounts if a.get("id") != account_id]
            if active_account_id is not None:
                if not any(row["id"] == active_account_id for row in data["accounts"]):
                    raise ValueError("active_account_not_found")
                data["active_account_id"] = active_account_id
            elif was_active:
                data["active_account_id"] = data["accounts"][0]["id"]
            cleanup_pending = delete_account_directory(target, lambda: self.write_index(data))
            return AccountDeletionResult(data["active_account_id"], cleanup_pending)


def populate_account_combo(combo: QComboBox, accounts_index: dict, active_account_id: str) -> None:
    combo.blockSignals(True)
    combo.clear()
    active_index = 0
    for idx, account in enumerate(accounts_index.get("accounts", [])):
        combo.addItem(account.get("name") or account.get("id"), account.get("id"))
        if account.get("id") == active_account_id:
            active_index = idx
    combo.setCurrentIndex(active_index)
    combo.blockSignals(False)


def show_account_manager_dialog(parent, style_sheet: str, manager: AccountManager, active_account_id: str, switch_account_callback, refresh_account_combo_callback) -> None:
    dialog = QDialog(parent)
    dialog.setWindowTitle("管理账号")
    dialog.setMinimumSize(460, 220)
    dialog.setStyleSheet(style_sheet)

    layout = QVBoxLayout(dialog)
    layout.setSpacing(10)

    row = QHBoxLayout()
    row.addWidget(QLabel("账号"))
    combo = QComboBox()
    row.addWidget(combo, 1)
    layout.addLayout(row)

    name_edit = QLineEdit()
    name_edit.setPlaceholderText("账号名称")
    layout.addWidget(name_edit)

    btn_row = QHBoxLayout()
    add_btn = QPushButton("新建")
    add_btn.setObjectName("btnPrimary")
    rename_btn = QPushButton("保存命名")
    rename_btn.setObjectName("btnAction")
    delete_btn = QPushButton("删除")
    export_btn = QPushButton("导出数据")
    export_btn.setObjectName("btnAction")
    import_btn = QPushButton("导入数据")
    import_btn.setObjectName("btnAction")
    delete_btn.setObjectName("btnDanger")
    close_btn = QPushButton("关闭")
    for btn in (rename_btn, export_btn, import_btn, add_btn, delete_btn):
        btn_row.addWidget(btn)
    btn_row.addStretch()
    btn_row.addWidget(close_btn)
    layout.addLayout(btn_row)

    runtime = {"active_id": active_account_id}

    def refresh(select_id: str | None = None):
        target = select_id or runtime["active_id"]
        populate_account_combo(combo, manager.read_index(), target)
        current = manager.account_meta(combo.currentData())
        name_edit.setText(current.get("name", ""))

    def current_id():
        return combo.currentData()

    def on_combo_changed(_=None):
        account = manager.account_meta(current_id())
        name_edit.setText(account.get("name", ""))

    def add_account():
        name, ok = QInputDialog.getText(dialog, "新建账号", "请输入账号名称：")
        if not ok or not name.strip():
            return
        try:
            account_id = manager.create_account(name.strip())
        except Exception as exc:
            QMessageBox.warning(dialog, "新建账号未完成", f"原账号保持不变，请排除故障后重试。\n{exc}")
            return
        refresh(account_id)
        refresh_account_combo_callback()

    def report_cleanup(result: AccountDeletionResult) -> None:
        if result.cleanup_pending is not None:
            QMessageBox.warning(dialog, "账号已移除，文件清理待完成",
                                f"账号已从列表移除，部分文件仍被占用或目录状态已变化。\n请关闭程序后核对并清理：{result.cleanup_pending}")

    def remove_temporary_account(account_id: str) -> None:
        # 导入/切换完成与临时目录清理是两项结果；清理失败不反转已提交的当前账号。
        try:
            result = manager.delete_account(account_id)
        except Exception as exc:
            QMessageBox.warning(dialog, "临时账号清理待完成", f"当前账号保持不变，临时账号已保留，请稍后重试。\n{exc}")
            return
        report_cleanup(result)

    def rename_account():
        account_id = current_id()
        name = name_edit.text().strip()
        if not account_id or not name:
            return
        manager.rename_account(account_id, name)
        if account_id == runtime["active_id"]:
            switch_account_callback(account_id)
        refresh(account_id)
        refresh_account_combo_callback()

    def delete_account():
        account_id = current_id()
        try:
            target = manager.prepare_account_deletion(account_id)
        except AccountMutationRecoveryError as exc:
            QMessageBox.warning(dialog, "账号恢复待完成", str(exc))
            return
        except ValueError:
            QMessageBox.information(dialog, "删除账号", "账号状态已变化或只剩一个账号，请刷新后重试。")
            return
        ret = QMessageBox.question(
            dialog,
            "删除账号",
            f"确定删除账号「{target.account.get('name', account_id)}」及其库存、配装、截图等数据吗？\n此操作不可恢复。",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if ret != QMessageBox.Yes:
            return
        # 实际运行 owner 而非索引 active 决定是否先切换；回调和弹窗始终在锁外。
        if account_id == runtime["active_id"]:
            replacement_id = next((row.get("id") for row in manager.read_index().get("accounts", [])
                                   if row.get("id") != account_id), None)
            try:
                switched = replacement_id is not None and switch_account_callback(replacement_id) is True
            except Exception:
                switched = False
            if not switched:
                QMessageBox.warning(dialog, "删除账号", "账号切换尚未完成，删除未开始。原账号已保留，请重试切换。")
                return
            runtime["active_id"] = replacement_id
        try:
            result = manager.delete_account(account_id, expected=target, active_account_id=runtime["active_id"])
        except Exception as exc:
            refresh(runtime["active_id"])
            refresh_account_combo_callback()
            QMessageBox.warning(dialog, "删除账号", f"账号删除尚未完成：{exc}\n当前运行账号已保留，请刷新后重试。")
            return
        refresh(runtime["active_id"])
        refresh_account_combo_callback()
        report_cleanup(result)

    def export_account():
        account_id = current_id()
        if not account_id:
            return
        account = manager.account_meta(account_id)
        default_name = f"{manager.safe_account_id(account.get('name') or account_id)}_nte_account.zip"
        path, _ = QFileDialog.getSaveFileName(dialog, "导出账号数据", default_name, "NTE Account Export (*.zip)")
        if not path:
            return
        try:
            export_account_data(manager, account_id, Path(path))
        except Exception as exc:
            QMessageBox.critical(dialog, "导出账号数据", f"导出失败：{exc}")
            return
        QMessageBox.information(dialog, "导出账号数据", "当前账号数据已导出。")

    def import_account():
        path, _ = QFileDialog.getOpenFileName(dialog, "导入账号数据", "", "NTE Account Export (*.zip)")
        if not path:
            return
        temporary_account_id: str | None = None
        original_active_id = runtime["active_id"]

        def release_replaced_account(target_id: str) -> None:
            """Move runtime ownership away before Windows renames its folder."""

            nonlocal temporary_account_id
            if target_id != runtime["active_id"]:
                return
            temporary_account_id = manager.create_account("导入临时账号")
            if switch_account_callback(temporary_account_id) is not True:
                remove_temporary_account(temporary_account_id)
                temporary_account_id = None
                raise RuntimeError("无法停止当前账号任务，导入未开始")
            runtime["active_id"] = temporary_account_id

        try:
            imported_id = import_account_data(
                manager, Path(path), before_replace=release_replaced_account,
            )
        except Exception as exc:
            switch_pending = False
            if temporary_account_id is not None and isinstance(exc, AccountImportRecoveryError):
                # 文件系统补偿受阻时原目录可能仍是候选；不自动切入未恢复账号。
                switch_pending = True
                refresh(runtime["active_id"])
                refresh_account_combo_callback()
            elif temporary_account_id is not None:
                try:
                    switched = switch_account_callback(original_active_id) is True
                except Exception:
                    switched = False
                # 切换成功才释放临时 owner；False/抛错保留当前运行上下文与目录。
                if switched:
                    runtime["active_id"] = original_active_id
                    remove_temporary_account(temporary_account_id)
                else:
                    switch_pending = True
                    refresh(runtime["active_id"])
                    refresh_account_combo_callback()
            detail = "\n账号回切尚未完成，临时账号已保留，请重试切换。" if switch_pending else ""
            QMessageBox.critical(dialog, "导入账号数据", f"导入失败：{exc}{detail}")
            return
        try:
            switched = switch_account_callback(imported_id) is True
        except Exception:
            switched = False
        if not switched:
            # 数据导入与运行时切换是两项结果，保留实际 owner，不把索引 active 当成切换。
            refresh(runtime["active_id"])
            refresh_account_combo_callback()
            QMessageBox.warning(dialog, "导入账号数据", "账号数据已导入，但账号切换尚未完成。当前账号已保留，请重试切换。")
            return
        runtime["active_id"] = imported_id
        if temporary_account_id is not None:
            remove_temporary_account(temporary_account_id)
        refresh(imported_id)
        refresh_account_combo_callback()
        QMessageBox.information(dialog, "导入账号数据", "账号数据已导入。")

    combo.currentIndexChanged.connect(on_combo_changed)
    add_btn.clicked.connect(add_account)
    rename_btn.clicked.connect(rename_account)
    export_btn.clicked.connect(export_account)
    import_btn.clicked.connect(import_account)
    delete_btn.clicked.connect(delete_account)
    close_btn.clicked.connect(dialog.accept)

    refresh()
    dialog.exec()
    refresh_account_combo_callback()
