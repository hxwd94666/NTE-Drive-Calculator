-- 静态库 v16：保存弧盘精炼描述占位符对应的官方等级曲线值。

CREATE TABLE fork_refinement_parameter_value (
    name_id TEXT NOT NULL,
    refinement_level INTEGER NOT NULL CHECK (refinement_level BETWEEN 1 AND 5),
    value REAL NOT NULL,
    source_row_id INTEGER NOT NULL REFERENCES source_row(source_row_id),
    PRIMARY KEY (name_id, refinement_level)
);

CREATE INDEX idx_fork_refinement_parameter_value_level
    ON fork_refinement_parameter_value(refinement_level, name_id);
