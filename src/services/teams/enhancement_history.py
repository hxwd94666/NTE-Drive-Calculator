# 读取冻结增强历史并说明当前引用状态，不重放伤害或重新解释旧数值。
from dataclasses import replace

from src.domain.localization.catalog import localized_text
from src.domain.teams.enhancement import saved_plan_fingerprint
from src.domain.teams.overview import SourceMode
from src.integrations.analysis_core.analysis_core_release import create_bundled_analysis_client


def current_component_provenance(static_database_path, cancelled):
    try:
        client = create_bundled_analysis_client(static_database_path=static_database_path, cancelled=cancelled)
        return ({**client.component_provenance, "engine_version": client.engine_version} if client else None)
    except (OSError, ValueError, RuntimeError):
        return None


def associate_enhancement(dao, saved, *, release, component, overview=None, text_catalog=None):
    value = saved.payload
    states = []
    team = next((team for team in dao.list_teams() if team.team_id == saved.team_id), None)
    current_equipment = team is not None and set(team.character_ids) == set(value["character_ids"])
    if not current_equipment:
        states.append(localized_text(text_catalog, "teams.enhancement_history.state.historical_lineup",
                                     "对应历史阵容"))
    if team:
        config = dao.get_team_source(team.team_id)
        if (config.mode != value["source_mode"] or config.revision != value["source_revision"]
                or [list(pair) for pair in config.bindings] != value["source_bindings"]):
            states.append(localized_text(text_catalog, "teams.enhancement_history.state.source_changed",
                                         "简报来源已改变"))
            current_equipment = False
        if config.mode == SourceMode.GAME:
            states.append(localized_text(
                text_catalog, "teams.enhancement_history.state.game_application_unverified",
                "保存方案与当前游戏配装的应用关系未确认"))
            current_equipment = False
    expected = {member.character_id: member for member in overview.members} if overview else {}
    for member in saved.members:
        slot = dao.get_loadout_slot(member["slot_id"])
        plan = dao.get_loadout_plan(member["plan_id"])
        if (slot is None or slot["is_archived"] or slot["character_id"] != member["character_id"]
                or slot["current_plan_id"] != member["plan_id"] or plan is None
                or saved_plan_fingerprint(plan) != member["fingerprint"]):
            current_equipment = False
        if overview:
            active = expected.get(member["character_id"])
            if active is None or active.slot_id != member["slot_id"] or active.plan_id != member["plan_id"]:
                current_equipment = False
    if current_equipment:
        states.insert(0, localized_text(text_catalog, "teams.enhancement_history.state.current_loadout",
                                        "对应当前所选计算配装"))
    else:
        states.insert(0, localized_text(text_catalog, "teams.enhancement_history.state.saved_plan",
                                        "对应历史保存方案"))
    if dao.load_battle_record(value["scope"]["record_id"]) is None:
        states.append(localized_text(text_catalog, "teams.enhancement_history.state.record_missing",
                                     "参考战报已删除／未随账号保留"))
    provenance = value["provenance"]
    if (provenance.get("dataset_id") != release.dataset_id or provenance.get("static_sha256") != release.sha256):
        states.append(localized_text(text_catalog, "teams.enhancement_history.state.static_changed",
                                     "当前静态资料与历史不同或历史字节身份未记录"))
    if component is None:
        states.append(localized_text(text_catalog, "teams.enhancement_history.state.component_pending",
                                     "当前分析组件来源待核对"))
    elif any(provenance.get(key) != component.get(key) for key in
             ("engine_version", "component_executable_sha256", "component_manifest_sha256")):
        states.append(localized_text(text_catalog, "teams.enhancement_history.state.component_changed",
                                     "当前分析组件与历史不同或历史来源未记录"))
    states.append(localized_text(text_catalog, "teams.enhancement_history.state.values_frozen",
                                 "数值保留当次冻结口径；重新分析须重新准备输入"))
    return replace(saved, association=tuple(dict.fromkeys(states)))


def latest_enhancement(dao, overview, *, release, component, text_catalog=None):
    rows = dao.list_team_enhancements(overview.team.team_id, limit=1, character_ids=overview.team.character_ids)
    return associate_enhancement(dao, rows[0], release=release, component=component, overview=overview,
                                 text_catalog=text_catalog) if rows else None
