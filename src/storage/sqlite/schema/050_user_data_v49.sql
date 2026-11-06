-- 轴伤数学基准、评价和显式选择；独立于源战报/库存的生命周期，只按队伍归属级联。
CREATE UNIQUE INDEX team_axis_target_identity ON team_axis_graduation_target(target_id,revision,team_id);

CREATE TABLE team_axis_graduation_benchmark (
    benchmark_id TEXT PRIMARY KEY,
    team_id TEXT NOT NULL REFERENCES team(team_id) ON DELETE CASCADE,
    target_id TEXT NOT NULL,
    target_revision INTEGER NOT NULL,
    content_sha256 TEXT NOT NULL CHECK(length(content_sha256)=64 AND content_sha256 NOT GLOB '*[^0-9a-f]*'),
    payload_json TEXT NOT NULL CHECK(typeof(payload_json)='text' AND length(CAST(payload_json AS BLOB))<=20971520),
    created_at_utc TEXT NOT NULL,
    UNIQUE(benchmark_id,team_id),
    FOREIGN KEY(target_id,target_revision,team_id) REFERENCES team_axis_graduation_target(target_id,revision,team_id) ON DELETE CASCADE,
    CHECK(CASE WHEN json_valid(payload_json) THEN (
        json_extract(payload_json,'$.format')='team_axis_graduation_saved_result_v1'
        AND json_extract(payload_json,'$.request.operation')='theoretical_benchmark'
        AND json_extract(payload_json,'$.request.target.target_id')=target_id
        AND json_extract(payload_json,'$.request.target.revision')=target_revision
        AND json_extract(payload_json,'$.request.target.team_id')=team_id
        AND json_extract(payload_json,'$.response.benchmark_reference.benchmark_id')=benchmark_id
        AND json_type(payload_json,'$.response.current_ratio_percent')='null'
        AND json_type(payload_json,'$.response.benchmark_reference.payload.reference_damage') IN ('integer','real')
        AND json_extract(payload_json,'$.response.benchmark_reference.payload.reference_damage')>0
        AND json_extract(payload_json,'$.response.benchmark_reference.payload.reference_damage')<=1.7976931348623157e308
    ) IS 1 ELSE 0 END)
);
CREATE INDEX team_axis_benchmark_history ON team_axis_graduation_benchmark(team_id,created_at_utc);

CREATE TABLE team_axis_graduation_evaluation (
    evaluation_id TEXT PRIMARY KEY,
    benchmark_id TEXT NOT NULL,
    team_id TEXT NOT NULL,
    operation TEXT NOT NULL CHECK(operation IN ('theoretical_benchmark','current_evaluation','recommendation_evaluation')),
    content_sha256 TEXT NOT NULL CHECK(length(content_sha256)=64 AND content_sha256 NOT GLOB '*[^0-9a-f]*'),
    payload_json TEXT CHECK(payload_json IS NULL OR (typeof(payload_json)='text' AND length(CAST(payload_json AS BLOB))<=20971520 AND json_valid(payload_json))),
    created_at_utc TEXT NOT NULL,
    UNIQUE(evaluation_id,benchmark_id,team_id),
    FOREIGN KEY(benchmark_id,team_id) REFERENCES team_axis_graduation_benchmark(benchmark_id,team_id) ON DELETE CASCADE,
    CHECK((operation='theoretical_benchmark' AND payload_json IS NULL) OR
          (operation!='theoretical_benchmark' AND payload_json IS NOT NULL)),
    CHECK(payload_json IS NULL OR CASE WHEN json_valid(payload_json) THEN (
        json_extract(payload_json,'$.format')='team_axis_graduation_saved_result_v1'
        AND json_extract(payload_json,'$.request.operation')=operation
        AND json_extract(payload_json,'$.request.current.team_id')=team_id
        AND json_extract(payload_json,'$.response.benchmark_reference.benchmark_id')=benchmark_id
        AND json_type(payload_json,'$.response.current_ratio_percent')='null'
    ) IS 1 ELSE 0 END),
    CHECK(evaluation_id='axis-evaluation-'||content_sha256)
);
CREATE INDEX team_axis_evaluation_history ON team_axis_graduation_evaluation(team_id,benchmark_id,created_at_utc);

CREATE TABLE team_axis_graduation_selection (
    team_id TEXT PRIMARY KEY REFERENCES team(team_id) ON DELETE CASCADE,
    benchmark_id TEXT NOT NULL,
    evaluation_id TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK(typeof(revision)='integer' AND revision>0),
    FOREIGN KEY(evaluation_id,benchmark_id,team_id) REFERENCES team_axis_graduation_evaluation(evaluation_id,benchmark_id,team_id) ON DELETE CASCADE
);
