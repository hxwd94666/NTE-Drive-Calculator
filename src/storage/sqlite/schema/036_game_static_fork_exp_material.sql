-- 静态库 v36：规范化弧盘升级经验材料及其单次使用消耗。

CREATE TABLE fork_exp_material (
    item_id TEXT PRIMARY KEY REFERENCES progression_item(item_id),
    experience_value INTEGER NOT NULL CHECK (experience_value > 0),
    source_row_id INTEGER NOT NULL REFERENCES source_row(source_row_id)
);

CREATE TABLE fork_exp_material_cost (
    item_id TEXT NOT NULL REFERENCES fork_exp_material(item_id),
    cost_item_id TEXT NOT NULL REFERENCES progression_item(item_id),
    quantity INTEGER NOT NULL CHECK (quantity > 0),
    PRIMARY KEY (item_id, cost_item_id)
);
