-- 静态库 v27：保存轨外怪物池条目的官方本地化名称。

ALTER TABLE abyss_monster_pool_entry
ADD COLUMN monster_name_zh TEXT;
