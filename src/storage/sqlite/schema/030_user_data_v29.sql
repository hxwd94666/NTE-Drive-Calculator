-- 用户数据库 v29：冻结目标倾陷上限，供倾陷逐击重放使用。

ALTER TABLE battle_target_condition
    ADD COLUMN enemy_topple_limit REAL NOT NULL DEFAULT 50
        CHECK (enemy_topple_limit >= 0);
