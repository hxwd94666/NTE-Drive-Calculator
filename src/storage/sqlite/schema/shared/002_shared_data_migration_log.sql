-- 本机公共覆盖库 v2：记录只执行一次的跨版本数据迁移。

CREATE TABLE data_migration (
    migration_key TEXT PRIMARY KEY,
    source_fingerprint TEXT,
    completed_at_utc TEXT NOT NULL,
    details_json TEXT NOT NULL
);
