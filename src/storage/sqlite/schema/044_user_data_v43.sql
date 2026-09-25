-- 客户端全部已观测物品的独立不可变快照；不引用装备库存当前指针。
CREATE TABLE all_item_snapshot (
    snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL CHECK (source = 'nte_core'),
    provider_id TEXT NOT NULL,
    domain_key TEXT NOT NULL,
    native_snapshot_id TEXT NOT NULL,
    revision TEXT NOT NULL,
    record_count INTEGER NOT NULL CHECK (record_count >= 0),
    collection_scope TEXT NOT NULL CHECK (collection_scope = 'all_observed_InventoryContainerMap_containers'),
    raw_snapshot_json TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    saved_at_utc TEXT NOT NULL,
    UNIQUE (provider_id, domain_key, native_snapshot_id)
);
