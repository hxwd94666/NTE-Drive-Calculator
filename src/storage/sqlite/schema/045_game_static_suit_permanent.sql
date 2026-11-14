-- 套装逐效果审查及无条件常驻属性；来源变化必须重新审查，不从描述补造数值。
CREATE TABLE equipment_suit_effect_review (
    suit_id TEXT NOT NULL, required_count INTEGER NOT NULL CHECK(required_count IN (2,4)),
    classification TEXT NOT NULL CHECK(classification IN ('permanent_only','mixed','conditional_only')),
    evidence_sha256 TEXT NOT NULL CHECK(length(evidence_sha256)=64),
    policy_sha256 TEXT NOT NULL CHECK(length(policy_sha256)=64),
    source_effect_id TEXT NOT NULL REFERENCES combat_effect_definition(effect_definition_id),
    note TEXT NOT NULL, PRIMARY KEY(suit_id,required_count),
    FOREIGN KEY(suit_id,required_count) REFERENCES equipment_suit_effect(suit_id,required_count)
);
CREATE TABLE equipment_suit_permanent_property (
    suit_id TEXT NOT NULL, required_count INTEGER NOT NULL,
    property_id TEXT NOT NULL CHECK(length(trim(property_id)) > 0),
    value REAL NOT NULL, unit TEXT NOT NULL CHECK(unit='ratio'),
    operation TEXT NOT NULL CHECK(operation='additive'),
    source_kind TEXT NOT NULL CHECK(source_kind IN ('modify_pack','buff_modifier')),
    source_key TEXT NOT NULL, source_ordinal INTEGER NOT NULL, curve_id TEXT,
    PRIMARY KEY(suit_id,required_count,property_id),
    FOREIGN KEY(suit_id,required_count) REFERENCES equipment_suit_effect_review(suit_id,required_count)
);
