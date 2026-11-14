# 指纹只覆盖仓库实际消费的账号域、当前锁和启用偏好版本，不随无关历史增长。
from contextlib import closing, nullcontext
from hashlib import sha256
import json
from pathlib import Path
import sqlite3

from src.domain.official_role.models import WORLD_BONUS_SETTING_KEY

_LATEST_VERSIONS = (
    "SELECT (SELECT v.profile_version_id FROM optimization_preference_version v "
    "WHERE v.profile_id=p.profile_id ORDER BY v.version_number DESC LIMIT 1) "
    "FROM optimization_preference_profile p WHERE p.is_active=1"
)
_LOCKED_PLANS = (
    "SELECT plan_id FROM loadout_plan WHERE allocation_locked=1 AND plan_id IN ("
    "SELECT current_plan_id FROM role_loadout_slot WHERE is_archived=0 AND current_plan_id IS NOT NULL)"
)


def _tables(db):
    present = {row[0] for row in db.execute("SELECT name FROM sqlite_schema WHERE type='table'")}
    return sorted(name for name in present if (
        name.startswith(("character_", "custom_character", "optimization_preference"))
        and name != "character_instance_mapping"
    ) or name in {"loadout_plan", "loadout_plan_item", "role_loadout_slot", "application_setting_copy", "database_profile"})


def _selection(name, columns):
    if name == "optimization_preference_profile":
        return " WHERE is_active=1", ()
    if name == "optimization_preference_version" or name.startswith("optimization_preference") and "profile_version_id" in columns:
        return f" WHERE profile_version_id IN ({_LATEST_VERSIONS})", ()
    if name in {"loadout_plan", "loadout_plan_item"}:
        return f" WHERE plan_id IN ({_LOCKED_PLANS})", ()
    if name == "role_loadout_slot":
        return f" WHERE is_archived=0 AND current_plan_id IN ({_LOCKED_PLANS})", ()
    if name == "application_setting_copy":
        return " WHERE setting_key=?", (WORLD_BONUS_SETTING_KEY,)
    return "", ()


def recommendation_account_identity(path: Path, *, read_unit=None) -> str:
    """冻结时借用同一读事务；提交核对新开只读事务，不水合全部历史方案。"""
    if read_unit is not None and read_unit.database_path != Path(path).resolve():
        raise ValueError("冻结库存身份不一致")
    owner = nullcontext(read_unit._db()) if read_unit is not None else closing(
        sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True))
    with owner as db:
        if read_unit is None:
            db.execute("BEGIN")
        digest = sha256()
        for name in _tables(db):
            quoted = '"' + name.replace('"', '""') + '"'
            # 默认权重的时间字段决定它是否仍属可刷新缓存，属于数值来源语义。
            columns = [row[1] for row in db.execute("PRAGMA table_info(" + quoted + ")")
                       if not row[1].endswith("_at_utc") or name == "character_weight_preference_seed"]
            where, parameters = _selection(name, columns)
            if name == "database_profile":
                columns = ["account_id"]
            projection = ",".join('"' + column.replace('"', '""') + '"' for column in columns)
            rows = sorted(json.dumps(tuple(row), ensure_ascii=False, default=str)
                          for row in db.execute("SELECT " + projection + " FROM " + quoted + where, parameters))
            digest.update(json.dumps([name, rows], ensure_ascii=False).encode())
        return digest.hexdigest()


def validate_warehouse_input_relations(user, snapshot_id):
    """核对当前消费域的外键闭包；不为检查无关历史而重建整套账号库。"""
    connection = user._db()
    names = [*_tables(connection), 'inventory_item', 'inventory_item_stat', 'inventory_item_runtime_state']
    def quote(name):
        return '"' + name.replace('"', '""') + '"'
    for name in names:
        columns = [row[1] for row in connection.execute('PRAGMA table_info(' + quote(name) + ')')]
        where, parameters = _selection(name, columns)
        if name.startswith('inventory_'):
            where, parameters = ' WHERE snapshot_id=?', (snapshot_id,)
        if name == 'role_loadout_slot':
            where, parameters = ' WHERE is_archived=0', ()
        keys = {}
        for row in connection.execute('PRAGMA foreign_key_list(' + quote(name) + ')'):
            keys.setdefault(row[0], []).append(row)
        for group in keys.values():
            group = sorted(group, key=lambda row: row[1])
            parent = group[0][2]
            target = [row[4] for row in group]
            if any(column is None for column in target):
                target = [row[1] for row in sorted(connection.execute('PRAGMA table_info(' + quote(parent) + ')'),
                                                  key=lambda row: row[5]) if row[5]]
            nullable = ' AND '.join('child.' + quote(row[3]) + ' IS NOT NULL' for row in group)
            matching = ' AND '.join('parent.' + quote(column) + '=child.' + quote(row[3])
                                    for row, column in zip(group, target))
            query = ('SELECT 1 FROM ' + quote(name) + ' child' + where + (' AND ' if where else ' WHERE ')
                     + nullable + ' AND NOT EXISTS(SELECT 1 FROM ' + quote(parent) + ' parent WHERE ' + matching + ') LIMIT 1')
            if connection.execute(query, parameters).fetchone() is not None:
                raise ValueError("仓库计算输入的关联数据不完整")
    if connection.execute(
        'SELECT 1 FROM role_loadout_slot s LEFT JOIN loadout_plan p ON p.plan_id=s.current_plan_id '
        'WHERE s.is_archived=0 AND s.current_plan_id IS NOT NULL AND p.plan_id IS NULL LIMIT 1'
    ).fetchone() is not None:
        raise ValueError("仓库计算输入的关联数据不完整")


def warehouse_current_snapshot_id(user):
    """仓库只认可正式当前指针；标记不完整时不借旧快照补齐。"""
    row = user._db().execute('SELECT snapshot_id FROM inventory_snapshot WHERE is_current=1').fetchone()
    return int(row[0]) if row is not None else None
