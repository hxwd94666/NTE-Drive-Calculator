-- 静态库 v24：从正式限时任务保存轨外之境当前/后续轮换。

CREATE TABLE outer_realm_rotation (
    level_config_id TEXT PRIMARY KEY,
    starts_at_mainland TEXT NOT NULL,
    ends_at_mainland TEXT NOT NULL,
    inference_ordinal INTEGER UNIQUE CHECK (
        inference_ordinal IS NULL OR inference_ordinal IN (0, 1)
    ),
    source_row_id INTEGER NOT NULL REFERENCES source_row(source_row_id),
    CHECK (starts_at_mainland < ends_at_mainland)
);

CREATE INDEX idx_outer_realm_rotation_start
    ON outer_realm_rotation(starts_at_mainland DESC);
