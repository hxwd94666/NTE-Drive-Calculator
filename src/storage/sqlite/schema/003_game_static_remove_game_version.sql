-- 静态库 v3：游戏版本由 nte-core 负责校验，不再在本地数据集元信息中重复维护。
ALTER TABLE dataset DROP COLUMN game_version;
