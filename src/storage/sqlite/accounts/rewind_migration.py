# 在账号升级事务中将旧倒带设置自动复制三份；一次性迁移，失败保留原配置及版本。
from __future__ import annotations

import json

from src.domain.rewind.strategy_preferences import STRATEGIES, UI_KEY, initial_profiles, strategy_key


def migrate_rewind_copies(connection, stamp, *, after_legacy_import=False):
    keys = ('rewind_recommendation', 'rewind_dual_target', UI_KEY, *(strategy_key(k) for k in STRATEGIES))
    rows = connection.execute('SELECT setting_key,value_json FROM application_setting_copy WHERE setting_key IN ('
        + ','.join('?' for _ in keys) + ')', keys).fetchall()
    values = {row[0]: json.loads(row[1]) for row in rows}
    existing = {k: values.get(strategy_key(k)) for k in STRATEGIES}
    present = [v is not None for v in existing.values()]
    if any(present):
        if not all(present):
            raise ValueError('invalid_rewind_migration_state')
        # 新建空库先升结构再导入旧 JSON；仅允许尚未编辑的空初值被首次导入填充。
        if not (after_legacy_import and any(k in values for k in keys[:2]) and all(
                p.get('seed') == 'empty' and p.get('revision') == 0 for p in existing.values())):
            if after_legacy_import:
                connection.execute('DELETE FROM application_setting_copy WHERE setting_key IN (?,?)', keys[:2])
            return
    legacy, dual = values.get(keys[0], {}), values.get(keys[1], {})
    profiles = initial_profiles(legacy, dual)
    active = legacy.get('strategy', 'balanced')
    if active not in STRATEGIES:
        active = 'balanced'
    targets = {strategy_key(k): p for k, p in profiles.items()}
    targets[UI_KEY] = dict(version=1, strategy=active)
    for key, value in targets.items():
        connection.execute('INSERT INTO application_setting_copy(setting_key,value_json,updated_at_utc) VALUES (?,?,?) '
            'ON CONFLICT(setting_key) DO UPDATE SET value_json=excluded.value_json,updated_at_utc=excluded.updated_at_utc',
            (key, json.dumps(value, ensure_ascii=False, allow_nan=False), stamp))
    connection.execute('DELETE FROM application_setting_copy WHERE setting_key IN (?,?)', keys[:2])
