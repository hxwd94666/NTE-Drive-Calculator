-- 独立图鉴用途与不依赖限时任务的轨外配置元数据。
ALTER TABLE dataset_scope RENAME TO dataset_scope_previous;
CREATE TABLE dataset_scope (
    dataset_id TEXT PRIMARY KEY REFERENCES dataset(dataset_id),
    scope TEXT NOT NULL CHECK (scope IN ('game', 'role_page', 'reference'))
);
INSERT INTO dataset_scope SELECT * FROM dataset_scope_previous;
DROP TABLE dataset_scope_previous;

CREATE TABLE catalog_outer_realm_season (
    level_config_id TEXT PRIMARY KEY,
    season_name_zh TEXT NOT NULL,
    buff_id TEXT NOT NULL,
    buff_name_zh TEXT NOT NULL,
    description_zh TEXT NOT NULL,
    gameplay_effect_path TEXT,
    add_to_character INTEGER CHECK (add_to_character IN (0, 1)),
    season_source_row_id INTEGER NOT NULL REFERENCES source_row(source_row_id),
    buff_source_row_id INTEGER REFERENCES source_row(source_row_id)
);
