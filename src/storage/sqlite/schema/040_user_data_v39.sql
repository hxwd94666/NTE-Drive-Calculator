-- 用户数据库 v39：为 nte-core 时停区间保存可空的正式类型掩码。

ALTER TABLE battle_time_stop_interval
    ADD COLUMN pause_type_mask INTEGER
        CHECK (
            pause_type_mask IS NULL
            OR pause_type_mask BETWEEN 1 AND 4294967295
        );
