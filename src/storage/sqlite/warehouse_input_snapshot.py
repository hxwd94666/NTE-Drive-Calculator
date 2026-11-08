# 用单个只读事务物化仓库计算依赖，排除战报等账号历史，不调用整库备份。
from __future__ import annotations

from concurrent.futures import CancelledError
from contextlib import closing
from pathlib import Path
import sqlite3

from src.storage.sqlite.user_data_support import SCHEMA_VERSION


# 现有计算器按 DAO 读取这些领域。这里保留表结构和正式身份，不能把临时输入库导入账号。
# 配装来源及角色实例引用的快照也须保留，不能把旧方案偷偷改绑到当前库存。
INPUT_TABLES = frozenset({
    "schema_migration", "database_profile", "application_setting_copy",
    "character_profile", "character_profile_skill", "character_profile_awaken_effect",
    "character_profile_observation", "character_instance_mapping",
    "character_weight_preference_seed", "character_weight_preference_property",
    "character_shape_bonus_preference", "character_shape_bonus_preference_property",
    "custom_character", "custom_character_board_cell", "custom_character_shape_bonus",
    "custom_character_calculation_setting",
    "optimization_preference_profile", "optimization_preference_version",
    "optimization_preference_character", "optimization_preference_property_weight",
    "optimization_preference_substat_priority", "optimization_preference_property_limit",
    "optimization_preference_substat_blacklist", "optimization_preference_substat_behavior",
    "loadout_plan", "loadout_plan_item", "role_loadout_slot",
    "inventory_snapshot", "inventory_item", "inventory_item_stat", "inventory_item_runtime_state",
})
_INVENTORY_TABLES = frozenset({
    "inventory_snapshot", "inventory_item", "inventory_item_stat", "inventory_item_runtime_state",
})


def freeze_warehouse_inputs(source: Path, target: Path, *, snapshot_id=None,
                            cancel_check=lambda: False) -> int:
    """返回本次唯一库存 ID；后台写入不重启此读取，取消/失败不留下半份输入。"""
    source, target = Path(source).resolve(), Path(target).resolve()
    if source == target:
        raise ValueError("仓库计算输入不能覆盖账号数据库")

    def checkpoint():
        if cancel_check():
            raise CancelledError("仓库计算输入读取已取消")

    checkpoint()
    # 新建专属临时文件，绝不覆盖现有账号或其他任务的输入。
    with target.open("xb"):
        pass
    try:
        with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True, timeout=1)) as original:
            original.set_progress_handler(lambda: int(cancel_check()), 1000)
            original.execute("BEGIN")
            version = original.execute("SELECT MAX(version) FROM schema_migration").fetchone()[0]
            if version != SCHEMA_VERSION:
                raise ValueError("账号结构版本不匹配，未准备仓库计算输入")
            if snapshot_id is None:
                row = original.execute("SELECT snapshot_id FROM inventory_snapshot WHERE is_current=1").fetchone()
                snapshot_id = row[0] if row else None
            row = original.execute(
                "SELECT complete, declared_item_count, stored_item_count FROM inventory_snapshot WHERE snapshot_id=?",
                (snapshot_id,),
            ).fetchone()
            if row is None or row[0] != 1 or row[1] != row[2]:
                raise ValueError("缺少本次完整库存快照，未准备仓库计算输入")
            snapshot_id = int(snapshot_id)
            # 单次读事务固定当前库存、历史来源和角色关系；普通旧快照不进入计算输入。
            snapshot_ids = {snapshot_id}
            for query in (
                "SELECT source_snapshot_id FROM loadout_plan",
                "SELECT first_seen_snapshot_id FROM character_instance_mapping",
                "SELECT last_seen_snapshot_id FROM character_instance_mapping",
            ):
                snapshot_ids.update(r[0] for r in original.execute(query) if r[0] is not None)
            schema = original.execute(
                "SELECT type,name,tbl_name,sql FROM sqlite_schema WHERE sql IS NOT NULL "
                "AND type IN ('table','index','view') ORDER BY type='table' DESC, name"
            ).fetchall()
            present = {name for kind, name, _, _ in schema if kind == "table"}
            if not INPUT_TABLES <= present:
                raise ValueError("账号缺少仓库计算所需的数据表")
            with closing(sqlite3.connect(target)) as frozen:
                frozen.execute("BEGIN")
                for kind, name, table, sql in schema:
                    if (kind == "table" and name in INPUT_TABLES or
                            kind == "index" and table in INPUT_TABLES or
                            kind == "view" and name == "current_inventory_item"):
                        checkpoint()
                        frozen.execute(sql)
                for table in sorted(INPUT_TABLES):
                    checkpoint()
                    parameters = tuple(sorted(snapshot_ids)) if table in _INVENTORY_TABLES else ()
                    where = " WHERE snapshot_id IN (" + ",".join("?" for _ in parameters) + ")" if parameters else ""
                    cursor = original.execute(f'SELECT * FROM "{table}"' + where, parameters)
                    insert = f'INSERT INTO "{table}" VALUES (' + ",".join("?" for _ in cursor.description) + ")"
                    while rows := cursor.fetchmany(256):
                        checkpoint()
                        frozen.executemany(insert, rows)
                # 这里只改变临时输入的选择指针，让旧调用方也消费已固定的页面快照。
                frozen.execute("UPDATE inventory_snapshot SET is_current=0 WHERE is_current=1")
                frozen.execute("UPDATE inventory_snapshot SET is_current=1 WHERE snapshot_id=?", (snapshot_id,))
                if frozen.execute("PRAGMA foreign_key_check").fetchone() is not None:
                    raise ValueError("仓库计算输入的关联数据不完整")
                count = frozen.execute("SELECT COUNT(*) FROM inventory_item WHERE snapshot_id=?", (snapshot_id,)).fetchone()[0]
                if count != row[2]:
                    raise ValueError("仓库计算输入的库存数量不一致")
                checkpoint()
                frozen.commit()
        return snapshot_id
    except BaseException as error:
        target.unlink(missing_ok=True)
        if isinstance(error, sqlite3.OperationalError) and cancel_check():
            raise CancelledError("仓库计算输入读取已取消") from error
        raise
