-- 用户数据库 v8：仅保存账号修改后的设置副本。

CREATE TABLE application_setting_copy (
    setting_key TEXT PRIMARY KEY,
    value_json TEXT NOT NULL CHECK (json_valid(value_json)),
    updated_at_utc TEXT NOT NULL
);

CREATE TABLE application_setting_migration (
    singleton_id INTEGER PRIMARY KEY CHECK (singleton_id = 1),
    legacy_imported_at_utc TEXT NOT NULL
);
