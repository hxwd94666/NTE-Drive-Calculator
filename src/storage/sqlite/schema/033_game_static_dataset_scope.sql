-- 区分完整游戏数据集与仅供角色页使用的独立目录。
CREATE TABLE dataset_scope (
    dataset_id TEXT PRIMARY KEY REFERENCES dataset(dataset_id),
    scope TEXT NOT NULL CHECK (scope IN ('game', 'role_page'))
);
