-- 静态库 v7：官方技能伤害执行参数；只保存数据，不在 SQLite 中推导伤害公式。

CREATE TABLE skill_damage (
    damage_id TEXT PRIMARY KEY,
    ability_id TEXT,
    damage_type TEXT NOT NULL,
    charge_add REAL NOT NULL,
    unbal_value REAL NOT NULL,
    heterochrome_add REAL NOT NULL,
    damage_source_category TEXT NOT NULL,
    fixed_crit_rate REAL NOT NULL,
    atk_rate_base_json TEXT NOT NULL,
    def_rate_base_json TEXT NOT NULL,
    hp_rate_base_json TEXT NOT NULL,
    story_balance_ge_rate REAL NOT NULL,
    attack_break_level TEXT NOT NULL,
    override_breakable_damage INTEGER NOT NULL CHECK (override_breakable_damage IN (0, 1)),
    breakable_damage REAL NOT NULL,
    override_breakable_impulse INTEGER NOT NULL CHECK (override_breakable_impulse IN (0, 1)),
    breakable_impulse REAL NOT NULL,
    override_vehicle_breakable_impulse INTEGER NOT NULL CHECK (override_vehicle_breakable_impulse IN (0, 1)),
    vehicle_breakable_impulse REAL NOT NULL,
    source_row_id INTEGER NOT NULL REFERENCES source_row(source_row_id)
);

CREATE INDEX idx_skill_damage_ability
    ON skill_damage(ability_id, damage_id);

CREATE TABLE skill_damage_modifier (
    damage_id TEXT PRIMARY KEY REFERENCES skill_damage(damage_id),
    atk_rate_base_coefficient REAL NOT NULL,
    source_row_id INTEGER NOT NULL REFERENCES source_row(source_row_id)
);
