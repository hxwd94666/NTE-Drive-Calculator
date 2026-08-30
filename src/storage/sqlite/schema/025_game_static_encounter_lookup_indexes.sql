-- 静态库 v25：为不区分大小写的怪物模板与等级变体查询建立表达式索引。

CREATE INDEX idx_monster_template_binding_name_nocase
    ON monster_template_binding(
        lower(monster_template_name), binding_kind, monster_manual_id
    );

CREATE INDEX idx_monster_instance_profile_lookup_nocase
    ON monster_instance_profile(static_table, lower(monster_id));

CREATE INDEX idx_monster_instance_profile_variant_lookup_nocase
    ON monster_instance_profile_variant(
        static_table, lower(monster_id), variant_kind, threshold_level
    );
