-- 用户数据库 v9：保存账号级界面项目顺序。

CREATE TABLE ui_item_order (
    scope TEXT NOT NULL,
    item_key TEXT NOT NULL,
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
    updated_at_utc TEXT NOT NULL,
    PRIMARY KEY (scope, item_key),
    UNIQUE (scope, ordinal)
);

CREATE INDEX idx_ui_item_order_scope
    ON ui_item_order(scope, ordinal);
