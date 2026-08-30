-- 静态库 v29：官方 Boss 支援表中的正式怪物模板成员。

CREATE TABLE monster_boss_support (
    monster_template_name TEXT PRIMARY KEY COLLATE NOCASE CHECK (
        length(trim(monster_template_name)) > 0
    ),
    description TEXT,
    source_row_id INTEGER NOT NULL REFERENCES source_row(source_row_id)
);
