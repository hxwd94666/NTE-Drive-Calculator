-- 静态图鉴被动与固有能力的正式角色归属，不声明战报可计算能力。
CREATE TABLE catalog_character_passive (
    character_id INTEGER NOT NULL REFERENCES character(character_id),
    ability_id TEXT NOT NULL,
    ability_type TEXT NOT NULL CHECK (ability_type IN ('Passive', 'Peculiarity')),
    ability_index INTEGER NOT NULL,
    unlock_stage INTEGER CHECK (unlock_stage BETWEEN 0 AND 6),
    source_row_id INTEGER NOT NULL REFERENCES source_row(source_row_id),
    PRIMARY KEY (character_id, ability_id)
);
