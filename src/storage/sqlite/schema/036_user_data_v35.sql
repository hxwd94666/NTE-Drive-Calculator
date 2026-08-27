-- 用户数据库 v35：冻结战报所选环境的逐目标生命、防御与抗性档案。

ALTER TABLE battle_target_condition
    ADD COLUMN selected_target_profiles_json TEXT NOT NULL DEFAULT '[]'
        CHECK (json_valid(selected_target_profiles_json));
