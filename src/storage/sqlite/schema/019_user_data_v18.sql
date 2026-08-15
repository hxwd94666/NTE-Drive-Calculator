-- 用户数据库 v18：自建角色的可编辑底盘与格位锁定。
CREATE TABLE IF NOT EXISTS custom_character_board_cell (
    character_id INTEGER NOT NULL REFERENCES custom_character(character_id) ON DELETE CASCADE,
    row_number INTEGER NOT NULL CHECK (row_number BETWEEN 1 AND 5),
    column_number INTEGER NOT NULL CHECK (column_number BETWEEN 1 AND 5),
    is_enabled INTEGER NOT NULL CHECK (is_enabled IN (0, 1)),
    is_locked INTEGER NOT NULL DEFAULT 0 CHECK (is_locked IN (0, 1)),
    PRIMARY KEY (character_id, row_number, column_number)
);
