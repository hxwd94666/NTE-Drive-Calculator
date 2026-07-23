# 管理账号创建、切换、导入和导出。
"""Account storage, migration, and account management dialog helpers."""

from __future__ import annotations

import json
import re
import shutil
import time
import zipfile
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

from src.storage.config_migration import migrate_core_config_dir
from src.storage.sqlite.user_data_dao import UserDataDao
from src.utils.logger import logger


TRANSFER_FORMAT_VERSION = 1
TRANSFER_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp"}
USER_DATABASE_FILENAME = "user_data.sqlite3"


@dataclass(frozen=True)
class AccountState:
    active_account_id: str
    active_account_name: str
    account_data_root: Path
    user_database_path: Path
    user_config_dir: Path
    screenshot_dir: Path
    log_dir: Path


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
        if self.accounts_index_file.exists():
            try:
                with open(self.accounts_index_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict) and data.get("accounts"):
                    return data
            except Exception as exc:
                logger.warning(f"账号索引读取失败，使用默认账号配置: {exc}")
        return {"active_account_id": "default", "accounts": [{"id": "default", "name": "默认账号"}]}

    def write_index(self, data: dict) -> None:
        self.accounts_dir.mkdir(parents=True, exist_ok=True)
        with open(self.accounts_index_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

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
        migrate_core_config_dir(self.config_dir, self.bundled_config_dir, self.core_config_files)

        src_templates = self.bundled_config_dir / "templates"
        if src_templates.exists() and src_templates.resolve() != self.template_dir.resolve():
            shutil.copytree(str(src_templates), str(self.template_dir), dirs_exist_ok=True)

    def seed_account_data(self, account_id: str, migrate_legacy: bool = False) -> None:
        account_root = self.account_dir(account_id)
        account_config = account_root / "config"
        for subdir in (account_config, account_root / "scanned_images", account_root / "logs"):
            subdir.mkdir(parents=True, exist_ok=True)

        # 用户数据库属于账号运行数据：首次创建账号时生成，不随安装包覆盖。
        account = self.account_meta(account_id)
        account_name = str(account.get("name") or account_id)
        with UserDataDao(
            account_root / USER_DATABASE_FILENAME,
            account_id=account_id,
            account_name=account_name,
        ) as user_database:
            if user_database.profile()["account_name"] != account_name:
                user_database.rename_account(account_name)

        legacy_config = self.data_root / "config"
        for fname in self.account_user_files:
            dst = account_config / fname
            if dst.exists():
                continue
            src = legacy_config / fname if migrate_legacy else None
            if src and src.exists() and src.resolve() != dst.resolve():
                shutil.copy2(str(src), str(dst))

        legacy_screenshots = self.data_root / "scanned_images"
        account_screenshots = account_root / "scanned_images"
        if migrate_legacy and legacy_screenshots.exists() and not any(account_screenshots.iterdir()):
            for file in self.iter_image_files(legacy_screenshots):
                try:
                    shutil.copy2(str(file), str(account_screenshots / file.name))
                except Exception as exc:
                    logger.warning(f"迁移旧截图失败，已跳过该文件: {file} | {exc}")

    def activate(self, account_id: str) -> AccountState:
        data = self.read_index()
        account = next((a for a in data.get("accounts", []) if a.get("id") == account_id), None)
        if not account:
            account = {"id": "default", "name": "默认账号"}
        active_id = account["id"]
        active_name = account.get("name") or active_id
        account_root = self.account_dir(active_id)
        self.seed_account_data(active_id)
        return AccountState(
            active_account_id=active_id,
            active_account_name=active_name,
            account_data_root=account_root,
            user_database_path=account_root / USER_DATABASE_FILENAME,
            user_config_dir=account_root / "config",
            screenshot_dir=account_root / "scanned_images",
            log_dir=account_root / "logs",
        )

    def initialize(self) -> AccountState:
        self.seed_user_config()
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
        self.seed_account_data(active_id, migrate_legacy=(active_id == "default"))
        return self.activate(active_id)

    def set_active_account_id(self, account_id: str) -> None:
        data = self.read_index()
        if not any(a.get("id") == account_id for a in data.get("accounts", [])):
            return
        data["active_account_id"] = account_id
        self.write_index(data)

    def create_account(self, name: str) -> str:
        data = self.read_index()
        existing = {a.get("id") for a in data.get("accounts", [])}
        base = self.safe_account_id(name)
        account_id = base
        suffix = 2
        while account_id in existing:
            account_id = f"{base}_{suffix}"
            suffix += 1
        data.setdefault("accounts", []).append({"id": account_id, "name": name.strip()})
        self.write_index(data)
        self.seed_account_data(account_id)
        return account_id

    def rename_account(self, account_id: str, name: str) -> None:
        data = self.read_index()
        for account in data.get("accounts", []):
            if account.get("id") == account_id:
                account["name"] = name
        self.write_index(data)
        database_path = self.account_dir(account_id) / USER_DATABASE_FILENAME
        if database_path.is_file():
            with UserDataDao(database_path) as user_database:
                user_database.rename_account(name)

    def delete_account(self, account_id: str) -> str | None:
        data = self.read_index()
        accounts = data.get("accounts", [])
        if len(accounts) <= 1:
            raise ValueError("at_least_one_account_required")
        was_active = data.get("active_account_id") == account_id
        data["accounts"] = [a for a in accounts if a.get("id") != account_id]
        new_active_id = None
        if was_active:
            new_active_id = data["accounts"][0]["id"]
            data["active_account_id"] = new_active_id
        self.write_index(data)
        shutil.rmtree(self.account_dir(account_id), ignore_errors=True)
        return new_active_id


def _safe_zip_member_path(root: Path, member: str) -> Path:
    target = (root / member).resolve()
    root = root.resolve()
    if root != target and root not in target.parents:
        raise ValueError(f"unsafe zip path: {member}")
    return target


def _write_tree_to_zip(zf: zipfile.ZipFile, source: Path, archive_root: str) -> None:
    if not source.exists():
        return
    for path in sorted(p for p in source.rglob("*") if p.is_file()):
        zf.write(path, f"{archive_root}/{path.relative_to(source).as_posix()}")


def export_account_data(manager: AccountManager, account_id: str, target_zip: Path) -> Path:
    """Export one account's user data plus the baseline screenshot."""

    account = manager.account_meta(account_id)
    source_root = manager.account_dir(account.get("id", account_id))
    if not source_root.exists():
        raise FileNotFoundError(source_root)

    target_zip = Path(target_zip)
    target_zip.parent.mkdir(parents=True, exist_ok=True)
    manifest = {
        "format": "nte-account-export",
        "version": TRANSFER_FORMAT_VERSION,
        "account": {"id": account.get("id", account_id), "name": account.get("name") or account_id},
    }
    with zipfile.ZipFile(target_zip, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        zf.writestr(
            "accounts.json",
            json.dumps(
                {"active_account_id": manifest["account"]["id"], "accounts": [manifest["account"]]},
                ensure_ascii=False,
                indent=2,
            ),
        )
        for child in sorted(source_root.iterdir()):
            if child.name == "scanned_images":
                continue
            if child.is_dir():
                _write_tree_to_zip(zf, child, f"account/{child.name}")
            elif child.is_file():
                zf.write(child, f"account/{child.name}")
        screenshot_dir = source_root / "scanned_images"
        for image in sorted(screenshot_dir.glob("raw_drive_0001.*")):
            if image.is_file() and image.suffix.lower() in TRANSFER_IMAGE_EXTS:
                zf.write(image, f"account/scanned_images/{image.name}")
    return target_zip


def import_account_data(manager: AccountManager, source_zip: Path) -> str:
    """Import one account export. Existing accounts with the same name are replaced."""

    source_zip = Path(source_zip)
    with zipfile.ZipFile(source_zip, "r") as zf:
        manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
        account = manifest.get("account") or {}
        account_name = str(account.get("name") or account.get("id") or "Imported").strip() or "Imported"
        original_id = str(account.get("id") or account_name)

        index = manager.read_index()
        accounts = list(index.get("accounts", []))
        existing = next((item for item in accounts if item.get("name") == account_name), None)
        if existing:
            target_id = existing.get("id") or manager.safe_account_id(account_name)
            existing["name"] = account_name
        else:
            existing_ids = {item.get("id") for item in accounts}
            base_id = manager.safe_account_id(original_id or account_name)
            target_id = base_id
            suffix = 2
            while target_id in existing_ids:
                target_id = f"{base_id}_{suffix}"
                suffix += 1
            accounts.append({"id": target_id, "name": account_name})

        target_root = manager.account_dir(target_id)
        shutil.rmtree(target_root, ignore_errors=True)
        target_root.mkdir(parents=True, exist_ok=True)

        for member in zf.infolist():
            if member.is_dir():
                continue
            name = member.filename.replace("\\", "/")
            if not name.startswith("account/"):
                continue
            relative = name.removeprefix("account/")
            target = _safe_zip_member_path(target_root, relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(member) as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst)

    index["accounts"] = accounts
    index["active_account_id"] = target_id
    manager.write_index(index)
    manager.seed_account_data(target_id)
    return target_id


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
    add_btn = QPushButton("添加")
    add_btn.setObjectName("btnAction")
    rename_btn = QPushButton("保存命名")
    rename_btn.setObjectName("btnAction")
    delete_btn = QPushButton("删除账号")
    export_btn = QPushButton("导出数据")
    export_btn.setObjectName("btnAction")
    import_btn = QPushButton("导入数据")
    import_btn.setObjectName("btnAction")
    delete_btn.setObjectName("btnDanger")
    close_btn = QPushButton("关闭")
    for btn in (add_btn, rename_btn, export_btn, import_btn, delete_btn):
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
        name, ok = QInputDialog.getText(dialog, "添加账号", "请输入账号名称：")
        if not ok or not name.strip():
            return
        account_id = manager.create_account(name.strip())
        refresh(account_id)
        refresh_account_combo_callback()

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
        data = manager.read_index()
        if len(data.get("accounts", [])) <= 1:
            QMessageBox.information(dialog, "删除账号", "至少需要保留一个账号。")
            return
        account = manager.account_meta(account_id)
        ret = QMessageBox.question(
            dialog,
            "删除账号",
            f"确定删除账号「{account.get('name', account_id)}」及其库存、配装、截图等数据吗？\n此操作不可恢复。",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if ret != QMessageBox.Yes:
            return
        new_active_id = manager.delete_account(account_id)
        if new_active_id:
            runtime["active_id"] = new_active_id
            switch_account_callback(new_active_id)
        refresh(new_active_id or runtime["active_id"])
        refresh_account_combo_callback()

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
        try:
            imported_id = import_account_data(manager, Path(path))
        except Exception as exc:
            QMessageBox.critical(dialog, "导入账号数据", f"导入失败：{exc}")
            return
        runtime["active_id"] = imported_id
        switch_account_callback(imported_id)
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
