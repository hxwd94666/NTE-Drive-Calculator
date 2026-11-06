-- 账号级轴伤毕业目标版本；只保存明确目标，不把数学参照冒充已完成毕业评价。
CREATE TABLE team_axis_graduation_target (
    target_id TEXT NOT NULL CHECK(length(target_id) BETWEEN 1 AND 256 AND trim(target_id)=target_id),
    team_id TEXT NOT NULL REFERENCES team(team_id) ON DELETE CASCADE,
    revision INTEGER NOT NULL CHECK(typeof(revision)='integer' AND revision>0),
    format_version INTEGER NOT NULL CHECK(format_version=1),
    target_sha256 TEXT NOT NULL CHECK(length(target_sha256)=64 AND target_sha256 NOT GLOB '*[^0-9a-f]*'),
    payload_json TEXT NOT NULL CHECK(typeof(payload_json)='text' AND length(CAST(payload_json AS BLOB))<=524288),
    created_at_utc TEXT NOT NULL,
    PRIMARY KEY(target_id, revision),
    UNIQUE(team_id, revision),
    CHECK(CASE WHEN json_valid(payload_json) THEN (
        json_type(payload_json)='object'
        AND json_extract(payload_json,'$.format')='team_axis_graduation_target_v1'
        AND json_type(payload_json,'$.target_id')='text' AND json_extract(payload_json,'$.target_id')=target_id
        AND json_type(payload_json,'$.team_id')='text' AND json_extract(payload_json,'$.team_id')=team_id
        AND json_type(payload_json,'$.revision')='integer' AND json_extract(payload_json,'$.revision')=revision
    ) IS 1 ELSE 0 END)
);
