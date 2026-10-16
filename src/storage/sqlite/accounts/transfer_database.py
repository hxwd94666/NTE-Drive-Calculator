# 以一致性副本处理账号包数据库；校验、升级和隐私字段清理均不触及活动库。
from __future__ import annotations

import json
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path
from time import monotonic

from src.storage.sqlite.user_data_dao import UserDataDao


PORTABLE_SETTING_FIELDS = {
    "identification_role_suits": frozenset({"version", "extra_suits", "suit_overrides"}),
    "update": frozenset({"never_remind", "ignored_version"}),
    "hotkeys": frozenset({"capture", "finish", "stop", "battle_rerecord"}),
    "sync": frozenset({"inventory_sync_method", "equipment_apply_method",
                       "inventory_settle_seconds", "inventory_snapshot_retention_count"}),
    "ui": frozenset({"log_enabled", "skip_unsaved_allocation_prompt",
                     "skip_automatic_assembly_duplicate_warning", "full_scan_capture_driver",
                     "full_scan_dual_thread_processing", "full_scan_amd_compatibility",
                     "full_scan_post_action_config", "protagonist_game_name",
                     "skip_protagonist_name_prompt"}),
}


def portable_settings(group: str, value: dict) -> dict:
    """旧主题/插件路径/风险确认/CDK 不随账号移动；其他业务组沿用原始事实。"""
    fields = PORTABLE_SETTING_FIELDS.get(group)
    if fields is not None:
        return {key: value[key] for key in fields if key in value}
    if group in {"work_mode", "plugins", "global_ui_preferences"}:
        return {}
    return value


def backup_account_database(source: Path, target: Path) -> None:
    deadline = monotonic() + 30.0

    def check_deadline(_status: int, _remaining: int, _total: int) -> None:
        if monotonic() > deadline:
            raise TimeoutError("账号数据库一致性备份超时，请稍后重试")

    with closing(sqlite3.connect(f"{source.resolve().as_uri()}?mode=ro", uri=True)) as original:
        with closing(sqlite3.connect(target)) as snapshot:
            original.backup(snapshot, pages=256, progress=check_deadline, sleep=0.05)


def _validate_structure(path: Path, connection: sqlite3.Connection) -> None:
    if connection.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
        raise ValueError("账号包用户数据库完整性校验失败")
    if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
        raise ValueError("账号包用户数据库外键校验失败")
    # 输入版本号不能代替正式 schema。新建参考库只用于当前候选的表/列核对，不留运行缓存。
    with tempfile.TemporaryDirectory(prefix=".schema-", dir=path.parent) as temporary:
        reference_path = Path(temporary) / "reference.sqlite3"
        with UserDataDao(reference_path, account_id="schema-check"):
            pass
        with closing(sqlite3.connect(reference_path)) as reference:
            query = ("SELECT type, name, sql FROM sqlite_schema "
                     "WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name")
            def schema(database):
                return [(kind, name, " ".join((sql or "").split()))
                        for kind, name, sql in database.execute(query)]
            # 不能只比较列：唯一当前库存索引、CHECK 和外键同样是账号事实约束。
            if schema(connection) != schema(reference):
                raise ValueError("账号包用户数据库与正式结构不一致")


def prepare_account_database(path: Path, *, account_id: str, account_name: str) -> None:
    """只在暂存副本升级；全部门禁通过后调用方才释放旧 owner 并安装目录。"""
    # 老库升级会生成本地历史迁移备份；它可能仍含旧 CDK，不能随候选目录安装。
    # 在独占子目录完成迁移，只取回已清理的主库；失败由外层丢弃本次候选。
    with tempfile.TemporaryDirectory(prefix=".upgrade-", dir=path.parent) as temporary:
        candidate = Path(temporary) / path.name
        path.replace(candidate)
        _prepare_database(candidate, account_id=account_id, account_name=account_name)
        candidate.replace(path)


def _prepare_database(path: Path, *, account_id: str, account_name: str) -> None:
    with closing(sqlite3.connect(path)) as connection:
        # 正式库只有 current_inventory_item 视图，没有触发器/虚拟表。
        # 候选中的额外执行对象在迁移前拒绝，正式视图在迁移后与参考结构比较。
        if connection.execute(
            "SELECT 1 FROM sqlite_schema WHERE type='trigger' "
            "OR (type='view' AND name!='current_inventory_item') "
            "OR lower(sql) LIKE '%create virtual table%'"
        ).fetchone() is not None:
            raise ValueError("账号包用户数据库包含非正式执行对象")
    with UserDataDao(path) as dao:
        profile = dao.profile()
        if profile["account_id"] != account_id:
            raise ValueError("账号包数据库与声明账号身份不一致")
        if profile["account_name"] != account_name:
            dao.rename_account(account_name)
    with closing(sqlite3.connect(path)) as connection:
        _validate_structure(path, connection)
        connection.execute("PRAGMA secure_delete = ON")
        with connection:
            for key, raw in connection.execute("SELECT setting_key, value_json FROM application_setting_copy").fetchall():
                value = json.loads(raw)
                if not isinstance(value, dict):
                    raise ValueError("账号包设置分组不是对象")
                cleaned = portable_settings(key, value)
                if cleaned != value:
                    connection.execute("UPDATE application_setting_copy SET value_json=? WHERE setting_key=?",
                                       (json.dumps(cleaned, ensure_ascii=False), key))
            connection.execute("UPDATE sync_settings SET capture_device_id=NULL, raw_capture_enabled=0, "
                               "auto_start_inventory_sync=0")
        # 清理副本空闲页中的旧 CDK；仅用于导出/导入副本，不 VACUUM 活动账号库。
        connection.execute("PRAGMA journal_mode = DELETE")
        connection.execute("VACUUM")
