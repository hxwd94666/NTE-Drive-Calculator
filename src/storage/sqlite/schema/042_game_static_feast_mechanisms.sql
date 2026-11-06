-- 保留赏宴挑战目标的独立成员、原始说明与效果归属，不推断完成或常驻生效。
ALTER TABLE feast_stage ADD COLUMN mechanism_category_title TEXT;
ALTER TABLE feast_stage ADD COLUMN mechanism_score_limit INTEGER CHECK (mechanism_score_limit >= 0);
ALTER TABLE feast_option ADD COLUMN description_zh TEXT;
CREATE TABLE feast_option_effect (
    option_id TEXT NOT NULL REFERENCES feast_option(option_id),
    recipient_kind TEXT NOT NULL CHECK (recipient_kind IN ('boss', 'player')),
    effect_ordinal INTEGER NOT NULL CHECK (effect_ordinal >= 0),
    buff_asset_path TEXT NOT NULL CHECK (length(trim(buff_asset_path)) > 0),
    source_row_id INTEGER NOT NULL REFERENCES source_row(source_row_id),
    PRIMARY KEY (option_id, recipient_kind, effect_ordinal)
);
