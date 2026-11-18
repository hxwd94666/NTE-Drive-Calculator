# 为扫描与仓库分别冻结评分上下文及角色范围；只复用基础评分，不编排状态决策。
from concurrent.futures import CancelledError
from pathlib import Path
from typing import Any
from src.domain.equipment.eligibility.matching import EquipmentMatchContext
from src.domain.equipment.eligibility.management_settings import MatchingScope
from src.services.identification.equipment_service import EquipmentIdentificationService
from src.storage.sqlite.static_game_data_dao import StaticGameDataDao


def prepare_management_scoring(config, inventory, *, config_dir, database, static_database, scope, cancel_check=None,
                               frozen_inputs=None):
    # 仓库只接受专用冻结请求；扫描继续保留自己的独立读取与匹配域，不提供旧仓库旁路。
    if (scope == MatchingScope.WAREHOUSE) != (frozen_inputs is not None):
        raise ValueError("冻结库存身份不一致")
    def checkpoint():
        if cancel_check is not None and cancel_check():
            raise CancelledError()

    checkpoint()
    scoring, context = None, EquipmentMatchContext()
    if any(config[name]["enabled"] for name in ("discard", "lock")):
        try:
            if frozen_inputs is not None:
                from src.services.inventory.recommendation.warehouse_calculation_inputs import warehouse_scoring
                scoring, context = warehouse_scoring(frozen_inputs, static_database, config_dir, cancel_check)
            else:
                matcher = EquipmentIdentificationService.from_paths(
                    config_dir=config_dir, user_database_path=database, static_database_path=static_database,
                    cancel_check=cancel_check, matching_scope=scope)
                scoring, context = matcher.scoring, matcher.context
            scoring.evaluate_global_inventory(inventory)
        except (ValueError, RuntimeError, OSError):
            scoring = None
            context = EquipmentMatchContext(errors=(("", "", "评分资料准备失败"),))
    checkpoint()
    roles, incomplete = {}, set()
    for name in ("discard", "lock"):
        module = config[name]
        roles[name] = []
        if scoring is not None and module["enabled"] and module["role_scope"] == "selected":
            try:
                roles[name] = selected_role_names(scoring.roles_db, module["selected_character_ids"], static_database)
            except (ValueError, RuntimeError, OSError):
                incomplete.add(name)
    return scoring, context, roles, frozenset(incomplete)


def selected_role_names(
    roles_db: dict[str, Any],
    selected_character_ids: list[int] | tuple[int, ...],
    static_database_path: str | Path | None = None,
) -> list[str]:
    """Resolve the management dialog's role IDs to scoring role names.

    Avatar variants share one logical role.  Resolving through the static
    logical key keeps a saved female/male avatar selection valid when the
    account snapshot later exposes the other official variant ID.
    """

    selected_ids: set[int] = set()
    for value in selected_character_ids:
        try:
            character_id = int(value)
        except (TypeError, ValueError):
            continue
        if character_id > 0:
            selected_ids.add(character_id)
    if not selected_ids:
        return []
    with (
        StaticGameDataDao(static_database_path) if static_database_path is not None else StaticGameDataDao()
    ) as static_dao:
        selected_keys = {
            static_dao.get_logical_character_key(character_id) or f"custom:{character_id}"
            for character_id in selected_ids
        }
        names: list[str] = []
        matched_keys: set[str] = set()
        for role_name, role in roles_db.items():
            try:
                character_id = int(role.get("character_id"))
            except (AttributeError, TypeError, ValueError):
                continue
            logical_key = static_dao.get_logical_character_key(character_id) or f"custom:{character_id}"
            if logical_key in selected_keys:
                names.append(str(role_name))
                matched_keys.add(logical_key)
        missing_count = len(selected_keys - matched_keys)
        if missing_count:
            raise ValueError(f"弃置/锁定管理中有 {missing_count} 名指定角色缺少可用评分配置，请重新选择角色")  # ui-text: internal
    return names
