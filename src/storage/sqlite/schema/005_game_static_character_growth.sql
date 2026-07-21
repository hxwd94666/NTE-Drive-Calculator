-- 静态库 v5：角色 1–80 级及六段突破后的官方基础生命、攻击和防御。

CREATE TABLE character_panel_growth (
    character_id INTEGER NOT NULL REFERENCES character(character_id),
    level INTEGER NOT NULL CHECK (level BETWEEN 1 AND 80),
    breakthrough_stage INTEGER NOT NULL CHECK (breakthrough_stage BETWEEN 0 AND 6),
    state TEXT NOT NULL CHECK (state IN ('normal', 'breakthrough_before', 'breakthrough_after', 'max_level')),
    hp_base REAL NOT NULL,
    atk_base REAL NOT NULL,
    def_base REAL NOT NULL,
    player_pack_source_row_id INTEGER NOT NULL REFERENCES source_row(source_row_id),
    level_modify_source_row_id INTEGER NOT NULL REFERENCES source_row(source_row_id),
    breakthrough_modify_source_row_id INTEGER REFERENCES source_row(source_row_id),
    PRIMARY KEY (character_id, level, breakthrough_stage)
);

CREATE INDEX idx_character_panel_growth_lookup
    ON character_panel_growth(character_id, level, breakthrough_stage);
