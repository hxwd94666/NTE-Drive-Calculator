-- 静态库 v32：将弧盘精炼中无条件的单条面板属性投影为可直接计算的数据。

CREATE TABLE fork_permanent_property (
    fork_id TEXT NOT NULL REFERENCES fork_item(fork_id),
    refinement_level INTEGER NOT NULL CHECK (refinement_level BETWEEN 1 AND 5),
    property_id TEXT NOT NULL CHECK (length(trim(property_id)) > 0),
    modifier_operation TEXT NOT NULL,
    property_value REAL NOT NULL,
    source_parameter_name_id TEXT NOT NULL,
    source_effect_definition_id TEXT NOT NULL
        REFERENCES combat_effect_definition(effect_definition_id),
    source_calculation_asset_path TEXT NOT NULL COLLATE NOCASE,
    source_row_id INTEGER NOT NULL REFERENCES source_row(source_row_id),
    PRIMARY KEY (fork_id, refinement_level)
);

CREATE INDEX idx_fork_permanent_property_property
    ON fork_permanent_property(property_id, fork_id, refinement_level);
