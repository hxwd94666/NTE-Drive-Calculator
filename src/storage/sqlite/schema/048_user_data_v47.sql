-- 队伍简报来源只属于账号；槽位引用保留稳定身份，删除槽位时不按名称重绑。
CREATE TABLE team_overview_source (
    team_id TEXT PRIMARY KEY NOT NULL REFERENCES team(team_id) ON DELETE CASCADE,
    mode TEXT NOT NULL CHECK(mode IN ('current', 'slots', 'game')),
    revision INTEGER NOT NULL CHECK(revision > 0)
);
CREATE TABLE team_overview_binding (
    team_id TEXT NOT NULL REFERENCES team_overview_source(team_id) ON DELETE CASCADE,
    character_id INTEGER NOT NULL CHECK(character_id > 0),
    slot_id INTEGER NOT NULL CHECK(slot_id > 0),
    PRIMARY KEY(team_id, character_id)
);
INSERT INTO team_overview_source SELECT team_id, 'current', 1 FROM team;

-- 历史正文不依赖槽位、方案或战报继续存在；只按队伍所有权清理自身结果。
CREATE TABLE team_enhancement_run (
    run_id TEXT PRIMARY KEY NOT NULL,
    format_version INTEGER NOT NULL CHECK(format_version=1),
    input_digest TEXT NOT NULL CHECK(length(input_digest)=64),
    created_at_utc TEXT NOT NULL,
    joint INTEGER NOT NULL CHECK(joint IN (0,1)),
    plan_ids_json TEXT NOT NULL CHECK(json_valid(plan_ids_json)),
    joint_summary_json TEXT CHECK(joint_summary_json IS NULL OR json_valid(joint_summary_json))
);
CREATE TABLE team_enhancement_result (
    result_id TEXT PRIMARY KEY NOT NULL,
    run_id TEXT NOT NULL REFERENCES team_enhancement_run(run_id) ON DELETE CASCADE,
    team_id TEXT NOT NULL REFERENCES team(team_id) ON DELETE CASCADE,
    payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),
    UNIQUE(run_id, team_id)
);
CREATE INDEX team_enhancement_by_team ON team_enhancement_result(team_id, run_id);
CREATE TABLE team_enhancement_member (
    result_id TEXT NOT NULL REFERENCES team_enhancement_result(result_id) ON DELETE CASCADE,
    character_id INTEGER NOT NULL CHECK(character_id>0),
    slot_id INTEGER NOT NULL CHECK(slot_id>0),
    plan_id INTEGER NOT NULL CHECK(plan_id>0),
    source_snapshot_id INTEGER NOT NULL CHECK(source_snapshot_id>0),
    fingerprint TEXT NOT NULL CHECK(length(fingerprint)=64),
    PRIMARY KEY(result_id, character_id)
);
