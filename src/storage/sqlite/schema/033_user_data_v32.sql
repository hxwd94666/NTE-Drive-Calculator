-- 用户数据库 v32：保存普通伤害抗性，旧条件沿用公式既有的 20% 默认值。

ALTER TABLE battle_target_condition
    ADD COLUMN resistance_normal REAL NOT NULL DEFAULT 0.2;
