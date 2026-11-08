-- 队伍仅保留所选计算槽位与游戏来源；旧自动来源在升级时一次冻结其原先解析的稳定槽位。
CREATE TEMP TABLE _team_source_v51 AS
SELECT team_id, CASE WHEN mode='current' THEN 'slots' ELSE mode END AS mode,
       revision + CASE WHEN mode='current' THEN 1 ELSE 0 END AS revision
FROM team_overview_source;
CREATE TEMP TABLE _team_binding_v51 AS SELECT * FROM team_overview_binding;
INSERT INTO _team_binding_v51(team_id, character_id, slot_id)
SELECT source.team_id, member.character_id,
       (SELECT slot_id FROM role_loadout_slot s
        WHERE s.character_id=member.character_id AND s.is_archived=0
        ORDER BY CASE WHEN s.slot_key='primary' THEN 0 ELSE 1 END, s.sort_order, s.slot_id LIMIT 1)
FROM team_overview_source source JOIN team_member member ON member.team_id=source.team_id
WHERE source.mode='current' AND EXISTS (
    SELECT 1 FROM role_loadout_slot s WHERE s.character_id=member.character_id AND s.is_archived=0
);
DROP TABLE team_overview_binding;
DROP TABLE team_overview_source;
CREATE TABLE team_overview_source (
    team_id TEXT PRIMARY KEY NOT NULL REFERENCES team(team_id) ON DELETE CASCADE,
    mode TEXT NOT NULL CHECK(mode IN ('slots', 'game')),
    revision INTEGER NOT NULL CHECK(revision > 0)
);
CREATE TABLE team_overview_binding (
    team_id TEXT NOT NULL REFERENCES team_overview_source(team_id) ON DELETE CASCADE,
    character_id INTEGER NOT NULL CHECK(character_id > 0),
    slot_id INTEGER NOT NULL CHECK(slot_id > 0),
    PRIMARY KEY(team_id, character_id)
);
INSERT INTO team_overview_source SELECT * FROM _team_source_v51;
INSERT INTO team_overview_binding SELECT * FROM _team_binding_v51;
DROP TABLE _team_binding_v51;
DROP TABLE _team_source_v51;
