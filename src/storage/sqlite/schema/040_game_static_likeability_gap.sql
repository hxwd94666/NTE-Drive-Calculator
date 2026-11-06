-- 静态库 v40：保留有正式好感引用但修改包缺失的字段级来源事实，不伪造属性或剔除角色。
CREATE TABLE character_likeability_gap (
    character_id INTEGER PRIMARY KEY REFERENCES character(character_id),
    required_level INTEGER NOT NULL CHECK (required_level = 10),
    modify_data_id TEXT NOT NULL CHECK (length(trim(modify_data_id)) > 0),
    source_row_id INTEGER NOT NULL REFERENCES source_row(source_row_id),
    reason TEXT NOT NULL CHECK (reason = 'missing_modifier_source')
);

CREATE TRIGGER character_likeability_gap_no_complete
BEFORE INSERT ON character_likeability_gap
WHEN EXISTS (SELECT 1 FROM character_likeability_bonus WHERE character_id = NEW.character_id)
BEGIN
    SELECT RAISE(ABORT, 'likeability cannot be complete and unavailable');
END;

CREATE TRIGGER character_likeability_bonus_no_gap
BEFORE INSERT ON character_likeability_bonus
WHEN EXISTS (SELECT 1 FROM character_likeability_gap WHERE character_id = NEW.character_id)
BEGIN
    SELECT RAISE(ABORT, 'likeability cannot be complete and unavailable');
END;
