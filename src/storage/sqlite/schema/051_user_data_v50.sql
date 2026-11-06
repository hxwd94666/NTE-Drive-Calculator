-- requires-foreign-keys-off
-- 装备/词条建议独立保存为同一参照下的评价；重建父表但保留全部旧正文、选择和CAS修订。
CREATE TABLE team_axis_graduation_evaluation_v50 (
    evaluation_id TEXT PRIMARY KEY,
    benchmark_id TEXT NOT NULL,
    team_id TEXT NOT NULL,
    operation TEXT NOT NULL CHECK(operation IN ('theoretical_benchmark','current_evaluation','recommendation_evaluation','equipment_advice_evaluation')),
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

INSERT INTO team_axis_graduation_evaluation_v50 SELECT * FROM team_axis_graduation_evaluation;
DROP TABLE team_axis_graduation_evaluation;
ALTER TABLE team_axis_graduation_evaluation_v50 RENAME TO team_axis_graduation_evaluation;
CREATE INDEX team_axis_evaluation_history ON team_axis_graduation_evaluation(team_id,benchmark_id,created_at_utc);
