# 只为账号有效匹配集合准备合法图纸，不读取计算页临时选择。
from __future__ import annotations

from concurrent.futures import CancelledError

from src.domain.equipment.matching import EquipmentMatchContext, RoleSuitMatch
from src.domain.suit_identity import suit_id_for_target
from src.services.identification.matching_identities import project_matching_settings
from src.services.identification.matching_settings import (
    matching_settings_identity, normalize_matching_settings, effective_matching_suits,
)


def build_equipment_match_context(orchestrator, settings=None, *, cancel_check=None, role_names=None):
    settings = normalize_matching_settings(settings)
    projected, missing_characters = project_matching_settings(
        orchestrator.roles_db, settings, getattr(orchestrator, "static_database_path", None))
    matches, unavailable, errors = [], [], []
    sets = orchestrator.sets_db
    # 目录中的名称只用来调用已有图纸适配器，保存与匹配均以正式套装 ID 为准。
    names_by_id = {str(row["suit_id"]): name for name, row in sets.items() if row.get("suit_id")}
    for role, data in orchestrator.roles_db.items():
        if role_names is not None and role not in role_names:
            continue
        if cancel_check and cancel_check():
            raise CancelledError("角色匹配套装准备已取消")
        character_id = data.get("character_id")
        if type(character_id) is not int or character_id <= 0:
            errors.append((role, "", "角色身份缺失"))
            continue
        default_id = suit_id_for_target(data.get("default_set"), sets)
        for suit_id in effective_matching_suits(projected, character_id, default_id):
            if not suit_id or suit_id not in names_by_id:
                errors.append((role, str(suit_id or ""), "套装资料缺失"))
                continue
            name = names_by_id[suit_id]
            try:
                blueprints = orchestrator.solve_blueprints(
                    [role], custom_sets={role: name}, cancel_check=cancel_check).get(role, [])
            except CancelledError:
                raise
            except (ValueError, KeyError, RuntimeError):
                errors.append((role, suit_id, "图纸准备失败"))
                continue
            if not blueprints:
                unavailable.append((role, suit_id, "没有合法图纸"))
                continue
            matches.append(RoleSuitMatch(
                role, character_id, suit_id, name, suit_id == default_id,
                frozenset(shape for bp in blueprints for shape in bp.get("set_pieces", ())),
                frozenset(shape for bp in blueprints for shape in bp.get("extra_pieces", ())),
            ))
    return EquipmentMatchContext(tuple(matches), tuple(unavailable), tuple(errors),
                                 matching_settings_identity(settings), missing_characters)
