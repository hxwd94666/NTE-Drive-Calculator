-- 用户数据库 v28：敌方条件可冻结实际怪物属性包防御，不再只按等级近似。

ALTER TABLE battle_target_condition
    ADD COLUMN enemy_defense_base REAL
        CHECK (enemy_defense_base IS NULL OR enemy_defense_base >= 0);

ALTER TABLE battle_target_condition
    ADD COLUMN enemy_defense_up REAL NOT NULL DEFAULT 0;

ALTER TABLE battle_target_condition
    ADD COLUMN enemy_defense_add REAL NOT NULL DEFAULT 0;
