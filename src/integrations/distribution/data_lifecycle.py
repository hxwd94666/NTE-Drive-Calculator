# 在独占数据根中编排升级恢复和显式迁入；原始数据与已产生的新数据都保留。
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
from uuid import uuid4

from src.app.constants import CORE_CONFIG_FILES
from src.storage.sqlite.runtime_data_snapshot import database_identity
from .data_inventory import account_databases, configuration_files, copy_snapshot_file
from .data_roots import write_record
from .lock import DataRootLease, reject_links
from .metadata import Distribution, DistributionError


def _digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _load(path: Path) -> dict:
    reject_links(path)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise DistributionError(f"数据维护记录损坏：{path}；请先恢复记录。") from error


def _databases(root: Path) -> list[tuple[Path, dict]]:
    result = [(path, database_identity(path, account_id)) for path, account_id in account_databases(root)]
    shared = root / "data" / "app_shared.sqlite3"
    for path in (shared, Path(str(shared) + "-wal"), Path(str(shared) + "-shm")):
        reject_links(path)
    if shared.exists():
        result.append((shared, database_identity(shared)))
    return result


def _make_snapshot(root: Path, files: list[Path], databases: list[tuple[Path, dict]],
                   distribution: Distribution, reason: str) -> Path:
    directory = root / "maintenance" / "data-recovery"
    reject_links(directory)
    directory.mkdir(parents=True, exist_ok=True)
    # 不自动删除可能仍需配套旧程序的恢复点；达到上限后先由用户归档再继续。
    if len(list(directory.iterdir())) >= 8:
        raise DistributionError("已有 8 份数据恢复记录，请先归档已验收版本的恢复点后重试。")
    target = directory / uuid4().hex
    target.mkdir()
    record = {"schema_version": 1, "recovery_id": target.name, "target_build_id": distribution.build_id,
              "reason": reason, "state": "preparing", "files": [], "databases": []}
    write_record(target / "record.json", record)
    try:
        for source in files:
            relative = source.relative_to(root).as_posix()
            copy_snapshot_file(source, target / "files" / relative)
            record["files"].append({"path": relative, "sha256": _digest(target / "files" / relative)})
        for source, identity in databases:
            relative = source.relative_to(root).as_posix()
            copy_snapshot_file(source, target / "files" / relative, database=True)
            record["databases"].append({"path": relative, **identity,
                                        "sha256": _digest(target / "files" / relative)})
        record["state"] = "prepared"
        write_record(target / "record.json", record)
    except BaseException:
        record["state"] = "backup_failed"
        write_record(target / "record.json", record)
        raise
    return target


def prepare_data_upgrade(root: Path, package: Path, distribution: Distribution,
                         lease: DataRootLease) -> Path | None:
    lease.require_root(root)
    for name in ("accounts", "config", "data", "logs", "scanned_images", "maintenance"):
        reject_links(root / name)
    pending = root / "maintenance" / "pending-upgrade.json"
    if pending.exists():
        identity = _load(pending).get("recovery_id", "unknown")
        raise DistributionError(f"上次数据升级未完成（恢复点 {identity}）；请显式恢复后再启动。")
    databases = _databases(root)
    for path, info in databases:
        if info["account_id"] is not None:
            for name in ("config", "logs", "scanned_images"):
                reject_links(path.parent / name)
    changing = [(path, info) for path, info in databases
                if info["schema"] < info["target_schema"] or info["legacy_settings"]]
    config_changed = any((root / "config" / name).is_file() and (package / "config" / name).is_file()
                         and _digest(root / "config" / name) != _digest(package / "config" / name)
                         for name in CORE_CONFIG_FILES)
    preferences = root / "config" / "global_ui_preferences.json"
    if databases and (not preferences.exists() or "theme" not in _load(preferences)):
        config_changed = True
    if not changing and not config_changed:
        return None
    snapshot = _make_snapshot(root, configuration_files(root), changing, distribution, "upgrade")
    write_record(pending, {"schema_version": 1, "recovery_id": snapshot.name})
    return snapshot


def upgrade_entries(snapshot: Path | None) -> list[dict]:
    return _load(snapshot / "record.json")["databases"] if snapshot else []


def finish_data_upgrade(root: Path, snapshot: Path | None, lease: DataRootLease) -> None:
    lease.require_root(root)
    if snapshot is None:
        return
    record = _load(snapshot / "record.json")
    # 各账号实际版本分别记载；当前账号启动成功不等于所有账号已迁移。
    record["observed_after_startup"] = [{"path": path.relative_to(root).as_posix(), **info}
                                       for path, info in _databases(root)]
    record["state"] = "startup_accepted"
    write_record(snapshot / "record.json", record)
    (root / "maintenance" / "pending-upgrade.json").unlink()


def restore_data_recovery(root: Path, recovery_id: str, distribution: Distribution,
                          lease: DataRootLease, *, confirm_replace: bool) -> Path:
    """显式恢复前先保存当前版本数据；恢复后退出，不直接用新程序再迁移。"""
    lease.require_root(root)
    if not confirm_replace or len(recovery_id) != 32 or any(c not in "0123456789abcdef" for c in recovery_id):
        raise DistributionError("恢复需要准确恢复点 ID，并明确确认替换当前数据。")
    snapshot = root / "maintenance" / "data-recovery" / recovery_id
    record = _load(snapshot / "record.json")
    if record.get("state") not in {"prepared", "startup_accepted", "restored"}:
        raise DistributionError("该恢复点未完成备份校验。")
    entries = record["files"] + record["databases"]
    for entry in entries:
        relative = Path(entry["path"])
        if relative.is_absolute() or ".." in relative.parts or relative.parts[0] not in {"accounts", "config", "data"}:
            raise DistributionError("恢复点包含越界路径。")
        source = snapshot / "files" / relative
        reject_links(source)
        reject_links(root / relative)
        if _digest(source) != entry["sha256"]:
            raise DistributionError("恢复点文件摘要不一致。")
    pending = root / "maintenance" / "pending-upgrade.json"
    interrupted = _load(pending) if pending.exists() else {}
    if interrupted.get("state") == "restoring":
        if interrupted.get("recovery_id") != recovery_id:
            raise DistributionError("已有另一恢复操作未完成，请先重试原恢复点。")
        preserved = root / "maintenance" / "data-recovery" / interrupted["preserved_newer_data"]
        preserved_record = _load(preserved / "record.json")
        if preserved_record.get("state") != "prepared":
            raise DistributionError("恢复前的新数据保全记录不完整。")
    else:
        preserved = _make_snapshot(root, configuration_files(root), _databases(root), distribution, "before_explicit_restore")
        write_record(pending, {"recovery_id": recovery_id, "state": "restoring", "preserved_newer_data": preserved.name})
    for entry in entries:
        destination = root / entry["path"]
        destination.parent.mkdir(parents=True, exist_ok=True)
        # 所有参与者已退出且独占根；清除的仅是本库已保全到一致性副本的 sidecar。
        if entry in record["databases"]:
            for suffix in ("-wal", "-shm"):
                sidecar = Path(str(destination) + suffix)
                reject_links(sidecar)
                sidecar.unlink(missing_ok=True)
        temporary = destination.with_name(f".{destination.name}.{uuid4().hex}.restore")
        shutil.copyfile(snapshot / "files" / entry["path"], temporary)
        os.replace(temporary, destination)
    # 新版新增配置已在 preserved 中保全，恢复旧配置集合时按确切路径移除，避免旧程序继续消费新字段。
    restored_paths = {entry["path"] for entry in entries}
    for entry in _load(preserved / "record.json")["files"]:
        if entry["path"] not in restored_paths:
            target = root / entry["path"]
            reject_links(target)
            target.unlink(missing_ok=True)
    record["state"] = "restored"
    record["preserved_newer_data"] = preserved.name
    write_record(snapshot / "record.json", record)
    (root / "maintenance" / "pending-upgrade.json").unlink()
    return preserved


def migrate_existing_data(source: Path, destination: Path, lease: DataRootLease,
                           *, confirm_source_closed: bool) -> None:
    """显式迁入完整本机用户事实；目标必须空，不合并账号，原根保持不变。"""
    lease.require_root(destination)
    source = Path(source).absolute()
    if not confirm_source_closed:
        raise DistributionError("请先退出旧程序，并使用 --confirm-source-closed 确认；历史旧版不遵守新锁。")
    if not source.is_dir() or source == destination or destination in source.parents:
        raise DistributionError("迁入源必须为独立、已存在的旧数据根。")
    if source in destination.parents and destination != source / "userdata":
        raise DistributionError("原目录迁入只接受独立 userdata 子目录，不接受旧数据子树内的目标。")
    if any((destination / name).exists() for name in ("accounts", "config", "data")):
        raise DistributionError("目标数据根已有用户数据，拒绝合并或覆盖。")
    with DataRootLease(source):
        databases = _databases(source)
        if not any(info["account_id"] is not None for _path, info in databases):
            raise DistributionError("所选旧根没有可核对身份的正式账号库。")
        files = configuration_files(source)
        for database, _key in account_databases(source):
            images = database.parent / "scanned_images"
            reject_links(images)
            if images.exists():
                for path in images.rglob("*"):
                    reject_links(path)
                    if path.is_file():
                        files.append(path)
        pending = destination / "maintenance" / "pending-import.json"
        if pending.exists():
            previous = _load(pending)
            if previous.get("state") != "preparing" or previous.get("source") != str(source):
                raise DistributionError("已有迁入待恢复，请使用 --resume-data-import，不重新读取其他来源。")
        stage = destination / "maintenance" / f"import-{uuid4().hex}"
        if len(list((destination / "maintenance").glob("import-*"))) >= 8:
            raise DistributionError("已有 8 份迁入暂存，请先归档失败暂存再重试。")
        stage.mkdir(parents=True)
        # 首次复制前登记：部分备份不是可发布集合，也不能让正常启动创建空账号。
        write_record(pending, {"schema_version": 1, "stage": stage.name, "state": "preparing", "source": str(source)})
        for path in files:
            copy_snapshot_file(path, stage / path.relative_to(source))
        for path, info in databases:
            target = stage / path.relative_to(source)
            copy_snapshot_file(path, target, database=True)
            database_identity(target, info["account_id"])
        # 先完整暂存；中途退出保留记录并阻止初始化，不将半份迁入当新账号。
        manifest = [{"path": path.relative_to(stage).as_posix(), "sha256": _digest(path)}
                    for path in stage.rglob("*") if path.is_file()]
        write_record(pending, {"schema_version": 1, "stage": stage.name, "state": "prepared", "files": manifest})
        resume_data_import(destination, lease)


def resume_data_import(root: Path, lease: DataRootLease) -> None:
    """按已完整暂存的确切域重试发布；中断后不重新读取旧来源。"""
    lease.require_root(root)
    pending = root / "maintenance" / "pending-import.json"
    record = _load(pending)
    if record.get("state") != "prepared":
        raise DistributionError("迁入暂存尚未完整；请退出旧程序后，用原 --migrate-data-from 来源显式重试。")
    name = record.get("stage", "")
    if not name.startswith("import-") or len(name) != 39 or any(c not in "0123456789abcdef" for c in name[7:]):
        raise DistributionError("迁入暂存身份无效。")
    stage = root / "maintenance" / name
    reject_links(stage)
    domains = {Path(row["path"]).parts[0] for row in record["files"]}
    if not domains.issubset({"accounts", "config", "data"}):
        raise DistributionError("迁入记录包含非用户数据域。")
    for row in record["files"]:
        relative = Path(row["path"])
        if relative.is_absolute() or ".." in relative.parts:
            raise DistributionError("迁入记录包含越界路径。")
        base = stage if (stage / relative.parts[0]).exists() else root
        path = base / relative
        reject_links(path)
        if _digest(path) != row["sha256"]:
            raise DistributionError("迁入暂存或已发布数据改变，停止恢复。")
    for domain in sorted(domains):
        if (stage / domain).exists():
            if (root / domain).exists():
                raise DistributionError("迁入目标已有冲突目录。")
            os.replace(stage / domain, root / domain)
    write_record(root / "maintenance" / "last-import.json", {
        "schema_version": 1, "state": "completed", "machine_paths_require_revalidation": True,
    })
    pending.unlink()
    stage.rmdir()
