-- 静态库 v30：角色发行注解、养成物品本地化目录与确定掉落投影。

CREATE TABLE character_release_evidence (
    evidence_key TEXT PRIMARY KEY,
    source_kind TEXT NOT NULL CHECK (
        source_kind IN ('official', 'reviewed_fallback')
    ),
    locator TEXT NOT NULL
);

CREATE TABLE character_release_annotation (
    character_id INTEGER PRIMARY KEY REFERENCES character(character_id),
    quality TEXT,
    quality_source_kind TEXT CHECK (
        quality_source_kind IS NULL OR
        quality_source_kind IN ('official', 'reviewed_fallback')
    ),
    acquisition_type TEXT CHECK (
        acquisition_type IS NULL OR
        acquisition_type IN ('permanent', 'limited', 'free')
    ),
    acquisition_source_kind TEXT CHECK (
        acquisition_source_kind IS NULL OR
        acquisition_source_kind IN ('official', 'reviewed_fallback')
    ),
    mainland_release_date TEXT,
    release_source_kind TEXT CHECK (
        release_source_kind IS NULL OR
        release_source_kind IN ('official', 'reviewed_fallback')
    ),
    official_source_row_id INTEGER REFERENCES source_row(source_row_id),
    CHECK ((quality IS NULL) = (quality_source_kind IS NULL)),
    CHECK ((acquisition_type IS NULL) = (acquisition_source_kind IS NULL)),
    CHECK ((mainland_release_date IS NULL) = (release_source_kind IS NULL))
);

CREATE TABLE character_release_evidence_link (
    character_id INTEGER NOT NULL REFERENCES character_release_annotation(character_id),
    field_name TEXT NOT NULL CHECK (
        field_name IN ('quality', 'acquisition_type', 'mainland_release_date')
    ),
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
    evidence_key TEXT NOT NULL REFERENCES character_release_evidence(evidence_key),
    PRIMARY KEY (character_id, field_name, ordinal),
    UNIQUE (character_id, field_name, evidence_key)
);

CREATE TABLE localized_term (
    entity_kind TEXT NOT NULL,
    canonical_id TEXT NOT NULL,
    source_kind TEXT NOT NULL CHECK (
        source_kind IN (
            'formal_localization', 'reviewed_annotation',
            'ui_state', 'name_missing'
        )
    ),
    text_table TEXT,
    text_key TEXT,
    source_row_id INTEGER REFERENCES source_row(source_row_id),
    PRIMARY KEY (entity_kind, canonical_id),
    CHECK (
        (text_table IS NULL AND text_key IS NULL) OR
        (text_table IS NOT NULL AND text_key IS NOT NULL)
    ),
    CHECK (
        source_kind <> 'name_missing' OR
        (text_table IS NULL AND text_key IS NULL)
    )
);

CREATE TABLE localized_term_name (
    entity_kind TEXT NOT NULL,
    canonical_id TEXT NOT NULL,
    locale TEXT NOT NULL,
    display_name TEXT NOT NULL CHECK (trim(display_name) <> ''),
    PRIMARY KEY (entity_kind, canonical_id, locale),
    FOREIGN KEY (entity_kind, canonical_id)
        REFERENCES localized_term(entity_kind, canonical_id)
);

CREATE TABLE character_acquisition_membership (
    character_id INTEGER NOT NULL REFERENCES character(character_id),
    acquisition_type TEXT NOT NULL CHECK (
        acquisition_type IN ('permanent', 'limited', 'free')
    ),
    source_kind TEXT NOT NULL CHECK (
        source_kind IN ('formal_game_data', 'reviewed_annotation')
    ),
    primary_source_row_id INTEGER REFERENCES source_row(source_row_id),
    supporting_source_row_id INTEGER REFERENCES source_row(source_row_id),
    evidence_key TEXT REFERENCES character_release_evidence(evidence_key),
    PRIMARY KEY (character_id, acquisition_type),
    CHECK (
        (source_kind = 'formal_game_data' AND primary_source_row_id IS NOT NULL
            AND evidence_key IS NULL) OR
        (source_kind = 'reviewed_annotation' AND primary_source_row_id IS NULL
            AND supporting_source_row_id IS NULL AND evidence_key IS NOT NULL)
    )
);

CREATE TABLE fork_lottery_campaign (
    pool_id TEXT PRIMARY KEY,
    featured_fork_id TEXT NOT NULL REFERENCES fork_item(fork_id),
    release_ordinal INTEGER NOT NULL UNIQUE CHECK (release_ordinal >= 0),
    title_text_table TEXT NOT NULL,
    title_text_key TEXT NOT NULL,
    source_row_id INTEGER NOT NULL REFERENCES source_row(source_row_id)
);

CREATE TABLE damage_resistance_term (
    resistance_id TEXT PRIMARY KEY,
    attribute_id TEXT NOT NULL UNIQUE,
    source_row_id INTEGER REFERENCES source_row(source_row_id)
);

CREATE TABLE progression_item (
    item_id TEXT PRIMARY KEY,
    names_json TEXT NOT NULL,
    name_status TEXT NOT NULL CHECK (name_status IN ('complete', 'name_missing')),
    name_zh TEXT,
    name_text_table TEXT,
    name_text_key TEXT,
    quality TEXT,
    icon_path TEXT,
    source_kind TEXT NOT NULL CHECK (
        source_kind IN ('official_item_catalog', 'referenced_missing')
    ),
    source_row_id INTEGER REFERENCES source_row(source_row_id),
    CHECK (
        (name_text_table IS NULL AND name_text_key IS NULL) OR
        (name_text_table IS NOT NULL AND name_text_key IS NOT NULL)
    ),
    CHECK (
        (source_kind = 'official_item_catalog' AND source_row_id IS NOT NULL) OR
        (source_kind = 'referenced_missing' AND source_row_id IS NULL)
    ),
    CHECK ((name_zh IS NULL) = (name_status = 'name_missing'))
);

CREATE TABLE progression_item_alias (
    token TEXT NOT NULL,
    context TEXT NOT NULL CHECK (
        context = 'progression_cost'
    ),
    item_id TEXT NOT NULL REFERENCES progression_item(item_id),
    source_kind TEXT NOT NULL CHECK (source_kind = 'product_contract'),
    PRIMARY KEY (token, context)
);

CREATE TABLE item_quality_term (
    quality_id TEXT PRIMARY KEY,
    grade_names_json TEXT NOT NULL,
    grade_zh TEXT NOT NULL,
    grade_text_table TEXT,
    grade_text_key TEXT,
    color_names_json TEXT NOT NULL,
    color_zh TEXT NOT NULL,
    color_text_table TEXT,
    color_text_key TEXT,
    source_row_id INTEGER NOT NULL REFERENCES source_row(source_row_id),
    CHECK (
        (grade_text_table IS NULL AND grade_text_key IS NULL) OR
        (grade_text_table IS NOT NULL AND grade_text_key IS NOT NULL)
    ),
    CHECK (
        (color_text_table IS NULL AND color_text_key IS NULL) OR
        (color_text_table IS NOT NULL AND color_text_key IS NOT NULL)
    )
);

CREATE TABLE clone_drop_projection (
    drop_id TEXT PRIMARY KEY,
    status TEXT NOT NULL CHECK (status IN ('complete', 'partial', 'unavailable')),
    source_kind TEXT NOT NULL CHECK (source_kind = 'official_drop_closure'),
    reason_code TEXT
);

CREATE TABLE clone_drop_projection_item (
    drop_id TEXT NOT NULL REFERENCES clone_drop_projection(drop_id),
    item_id TEXT NOT NULL REFERENCES progression_item(item_id),
    quantity INTEGER NOT NULL CHECK (quantity > 0),
    PRIMARY KEY (drop_id, item_id)
);

CREATE TABLE clone_drop_projection_gap (
    drop_id TEXT NOT NULL REFERENCES clone_drop_projection(drop_id),
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
    sequence_id TEXT,
    reason_code TEXT NOT NULL,
    source_row_id INTEGER REFERENCES source_row(source_row_id),
    PRIMARY KEY (drop_id, ordinal)
);

CREATE INDEX idx_clone_drop_projection_status
    ON clone_drop_projection(status, drop_id);
CREATE INDEX idx_clone_drop_projection_item
    ON clone_drop_projection_item(item_id, drop_id);
