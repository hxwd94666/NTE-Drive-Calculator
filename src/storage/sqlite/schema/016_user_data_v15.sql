-- 用户数据库 v15：角色可拥有多个已保存配装槽位。
CREATE TABLE role_loadout_slot (
    slot_id INTEGER PRIMARY KEY,
    character_id INTEGER NOT NULL,
    slot_key TEXT NOT NULL,
    slot_name TEXT NOT NULL,
    sort_order INTEGER NOT NULL CHECK (sort_order >= 0),
    current_plan_id INTEGER REFERENCES loadout_plan(plan_id) ON DELETE SET NULL,
    is_archived INTEGER NOT NULL DEFAULT 0 CHECK (is_archived IN (0, 1)),
    created_at_utc TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL,
    UNIQUE (character_id, slot_key),
    UNIQUE (character_id, sort_order)
);

ALTER TABLE loadout_plan
    ADD COLUMN slot_id INTEGER REFERENCES role_loadout_slot(slot_id) ON DELETE SET NULL;

DROP INDEX idx_loadout_plan_active_character;

CREATE UNIQUE INDEX idx_loadout_plan_active_slot
    ON loadout_plan(slot_id) WHERE is_active = 1 AND slot_id IS NOT NULL;
CREATE INDEX idx_loadout_plan_slot
    ON loadout_plan(slot_id, updated_at_utc DESC, plan_id DESC);
CREATE INDEX idx_role_loadout_slot_character
    ON role_loadout_slot(character_id, is_archived, sort_order, slot_id);

INSERT INTO role_loadout_slot(
    character_id, slot_key, slot_name, sort_order,
    current_plan_id, is_archived, created_at_utc, updated_at_utc
)
SELECT DISTINCT
    character_id, 'primary', '主力', 0,
    NULL, 0, created_at_utc, updated_at_utc
FROM loadout_plan
WHERE is_active = 1;

UPDATE loadout_plan
SET slot_id = (
    SELECT slot_id
    FROM role_loadout_slot AS slot
    WHERE slot.character_id = loadout_plan.character_id
      AND slot.slot_key = 'primary'
)
WHERE is_active = 1;

UPDATE role_loadout_slot
SET current_plan_id = (
    SELECT plan.plan_id
    FROM loadout_plan AS plan
    WHERE plan.slot_id = role_loadout_slot.slot_id
      AND plan.is_active = 1
    ORDER BY plan.updated_at_utc DESC, plan.plan_id DESC
    LIMIT 1
)
WHERE slot_key = 'primary';
