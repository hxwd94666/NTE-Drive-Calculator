-- 静态库 v21：保留自定义属性修正的条件组、标签约束与施加条件。

ALTER TABLE buff_modifier
    ADD COLUMN modifier_group_ordinal INTEGER NOT NULL DEFAULT 0
        CHECK (modifier_group_ordinal >= 0);

ALTER TABLE buff_modifier
    ADD COLUMN application_requirement_asset_path TEXT COLLATE NOCASE;

ALTER TABLE buff_modifier
    ADD COLUMN source_require_tags_json TEXT NOT NULL DEFAULT '[]'
        CHECK (json_valid(source_require_tags_json));

ALTER TABLE buff_modifier
    ADD COLUMN source_ignore_tags_json TEXT NOT NULL DEFAULT '[]'
        CHECK (json_valid(source_ignore_tags_json));

ALTER TABLE buff_modifier
    ADD COLUMN target_require_tags_json TEXT NOT NULL DEFAULT '[]'
        CHECK (json_valid(target_require_tags_json));

ALTER TABLE buff_modifier
    ADD COLUMN target_ignore_tags_json TEXT NOT NULL DEFAULT '[]'
        CHECK (json_valid(target_ignore_tags_json));

CREATE INDEX idx_buff_modifier_requirement
    ON buff_modifier(application_requirement_asset_path, asset_path);
