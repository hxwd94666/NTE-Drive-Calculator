-- 用户数据库 v37：角色档案显式保存弧盘突破阶段。

ALTER TABLE character_profile ADD COLUMN fork_breakthrough_stage INTEGER
    CHECK (
        fork_breakthrough_stage IS NULL
        OR fork_breakthrough_stage BETWEEN 0 AND 6
    );

ALTER TABLE battle_character_build_snapshot
    ADD COLUMN fork_breakthrough_stage INTEGER
    CHECK (
        fork_breakthrough_stage IS NULL
        OR fork_breakthrough_stage BETWEEN 0 AND 6
    );

ALTER TABLE battle_character_build_edit
    ADD COLUMN fork_breakthrough_stage INTEGER
    CHECK (
        fork_breakthrough_stage IS NULL
        OR fork_breakthrough_stage BETWEEN 0 AND 6
    );

-- 旧档案只有等级。在临界等级保留旧行为的“突破前”默认，
-- 非临界等级则回填达到该等级所必需的最小突破阶段。
UPDATE character_profile
SET fork_breakthrough_stage = CASE
    WHEN fork_id IS NULL OR fork_level IS NULL THEN NULL
    WHEN fork_level <= 20 THEN 0
    WHEN fork_level <= 30 THEN 1
    WHEN fork_level <= 40 THEN 2
    WHEN fork_level <= 50 THEN 3
    WHEN fork_level <= 60 THEN 4
    WHEN fork_level <= 70 THEN 5
    ELSE 6
END;
