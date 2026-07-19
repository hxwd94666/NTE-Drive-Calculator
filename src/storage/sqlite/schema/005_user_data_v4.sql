-- 用户数据库 v4：角色实例映射和可续跑的一键装配任务。

CREATE TABLE character_instance_mapping (
    character_id INTEGER NOT NULL CHECK (character_id > 0),
    uid_slot INTEGER NOT NULL CHECK (uid_slot > 0),
    uid_serial INTEGER NOT NULL CHECK (uid_serial > 0),
    source TEXT NOT NULL CHECK (source IN ('snapshot', 'manual')),
    first_seen_snapshot_id INTEGER
        REFERENCES inventory_snapshot(snapshot_id) ON DELETE SET NULL,
    last_seen_snapshot_id INTEGER
        REFERENCES inventory_snapshot(snapshot_id) ON DELETE SET NULL,
    created_at_utc TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL,
    PRIMARY KEY (character_id, uid_slot, uid_serial)
);

CREATE INDEX idx_character_instance_mapping_character
    ON character_instance_mapping(character_id, updated_at_utc DESC);

CREATE TABLE equipment_apply_job (
    job_id INTEGER PRIMARY KEY,
    source_snapshot_id INTEGER NOT NULL
        REFERENCES inventory_snapshot(snapshot_id) ON DELETE RESTRICT,
    status TEXT NOT NULL CHECK (status IN ('prepared', 'running', 'failed', 'completed')),
    created_at_utc TEXT NOT NULL,
    started_at_utc TEXT,
    completed_at_utc TEXT,
    last_error TEXT
);

CREATE INDEX idx_equipment_apply_job_status
    ON equipment_apply_job(status, created_at_utc DESC);

CREATE TABLE equipment_apply_job_item (
    job_item_id INTEGER PRIMARY KEY,
    job_id INTEGER NOT NULL REFERENCES equipment_apply_job(job_id) ON DELETE CASCADE,
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
    role_name TEXT NOT NULL,
    character_id INTEGER NOT NULL CHECK (character_id > 0),
    character_uid_json TEXT NOT NULL,
    plan_id INTEGER NOT NULL REFERENCES loadout_plan(plan_id) ON DELETE RESTRICT,
    status TEXT NOT NULL CHECK (status IN ('pending', 'running', 'succeeded', 'failed')),
    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    before_snapshot_id INTEGER REFERENCES inventory_snapshot(snapshot_id) ON DELETE SET NULL,
    after_snapshot_id INTEGER REFERENCES inventory_snapshot(snapshot_id) ON DELETE SET NULL,
    last_error TEXT,
    started_at_utc TEXT,
    completed_at_utc TEXT,
    UNIQUE (job_id, ordinal)
);

CREATE INDEX idx_equipment_apply_job_item_pending
    ON equipment_apply_job_item(job_id, status, ordinal);

CREATE TABLE equipment_apply_job_log (
    log_id INTEGER PRIMARY KEY,
    job_id INTEGER NOT NULL REFERENCES equipment_apply_job(job_id) ON DELETE CASCADE,
    job_item_id INTEGER REFERENCES equipment_apply_job_item(job_item_id) ON DELETE SET NULL,
    created_at_utc TEXT NOT NULL,
    level TEXT NOT NULL CHECK (level IN ('info', 'error')),
    message TEXT NOT NULL
);

CREATE INDEX idx_equipment_apply_job_log_created
    ON equipment_apply_job_log(job_id, log_id);
