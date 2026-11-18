# 在单个只读账号事务中物化仓库所需业务事实，计算阶段不持有或重开账号数据库。
from concurrent.futures import CancelledError
from dataclasses import replace
import json
from pathlib import Path

from src.domain.equipment.eligibility.management_settings import MatchingScope, normalize_warehouse_config
from src.domain.equipment.properties.stat_catalog import StatCatalog
from src.domain.equipment.warehouse.calculation_input import WarehouseCalculationInput
from src.integrations.allocation.native_blueprints import create_bundled_blueprint_executor
from src.integrations.static_catalog.static_catalog_release import static_catalog_read_unit
from src.models.equipment import DriveShape
from src.observability.stage_timings import timed_call
from src.services.allocation.execution.legacy_allocation_static_catalog import build_legacy_allocation_static_catalog
from src.services.allocation.inputs.inventory import SqliteAllocationInventory
from src.services.identification.matching_context import build_equipment_match_context
from src.services.identification.matching_settings import normalize_matching_settings, matching_settings_identity
from src.services.inventory.recommendation.warehouse_recommendation_profiles import materialize_profile_contexts
from src.solver.orchestrator import NTEPipelineOrchestrator
from src.optimizer.scoring import ScoringEngine
from src.storage.sqlite.inventory.warehouse_recommendation_inputs import (
    recommendation_account_identity, validate_warehouse_input_relations, warehouse_current_snapshot_id,
)
from src.storage.sqlite.user_data_dao import UserDataDao


def freeze_warehouse_calculation_inputs(database, static_database, config_dir, *, snapshot_id=None,
                                      include_profiles=False, cancel_check=lambda: False, timings=None):
    def checkpoint():
        if cancel_check():
            raise CancelledError("仓库计算输入读取已取消")

    checkpoint()
    database, config_dir = Path(database), Path(config_dir)
    try:
        with UserDataDao.read_only(database, cancel_check=cancel_check) as user, user.read_consistent_state(), \
                static_catalog_read_unit(static_database) as unit:
            identity = recommendation_account_identity(database, read_unit=user)
            selected = snapshot_id if snapshot_id is not None else warehouse_current_snapshot_id(user)
            summary = user.inventory_snapshot_summary(selected) if selected is not None else None
            if (summary is None or not summary['complete'] or summary['source'] not in {'nte_core', 'vision', 'gamepad'}
                    or summary['declared_item_count'] != summary['stored_item_count']):
                raise ValueError("缺少本次完整库存快照，未准备仓库计算输入")
            validate_warehouse_input_relations(user, int(selected))
            projection = SqliteAllocationInventory(user, unit.dao).build(selected)
            originals = user.list_inventory_items_with_runtime_state(selected)
            locked = user.list_allocation_locked_equipment_owners()
            copies = user.list_application_setting_copies()
            config = normalize_warehouse_config(copies.get('ui', {}).get(MatchingScope.WAREHOUSE.management_key))
            matching = normalize_matching_settings(copies.get(MatchingScope.WAREHOUSE.setting_key))
            checkpoint()
            catalog = timed_call(timings, "input.role_catalog", build_legacy_allocation_static_catalog,
                config_dir=config_dir, user_database_path=database, static_database_path=static_database,
                static_read_unit=unit, user_read_unit=user)
            checkpoint()
            contexts, issues = (materialize_profile_contexts(user, unit.dao, config_dir / 'stats.json',
                int(selected), checkpoint, timings=timings) if include_profiles else ((), ()))
            data = dict(projected=list(projection.items), originals=originals, locked_owners=locked,
                        roles=catalog.roles_db, sets=catalog.sets_db,
                        shapes={key: shape.model_dump() for key, shape in catalog.shapes_db.items()},
                        boards=catalog.board_matrices, fork_ids=catalog.fork_ids_by_name,
                        config=config, matching=matching,
                        item_names={str(row['item_id']): row['name_zh'] for row in unit.dao.list_equipment_items()})
            payload = json.dumps(data, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode()
            checkpoint()
            result = WarehouseCalculationInput(int(selected), identity, unit.release.dataset_id,
                unit.release.sha256, payload, contexts, issues, include_profiles)
        if timings is not None:
            timings.note("input.freeze", item_count=len(originals), snapshot_count=1, copied_bytes=len(payload))
        checkpoint()
        return result
    except BaseException as error:
        if cancel_check() and not isinstance(error, CancelledError):
            raise CancelledError("仓库计算输入读取已取消") from error
        raise


def warehouse_orchestrator(inputs, static_database, config_dir, cancel_check, *, blueprint_executor=None, timings=None):
    with static_catalog_read_unit(static_database) as unit:
        if (unit.release.dataset_id, unit.release.sha256) != (inputs.static_dataset_id, inputs.static_sha256):
            raise ValueError("仓库建议的配装、权重、资料或规则已变化，请重新生成建议")
    data = inputs.values()
    return NTEPipelineOrchestrator.from_frozen_inputs(
        roles_db=data['roles'], sets_db=data['sets'],
        shapes_db={key: DriveShape(**value) for key, value in data['shapes'].items()},
        board_matrices=data['boards'], fork_ids_by_name=data['fork_ids'], config_dir=config_dir,
        static_database_path=Path(static_database), timings=timings,
        blueprint_executor=(blueprint_executor if blueprint_executor is not None else
                            create_bundled_blueprint_executor(static_database, cancel_check=cancel_check)))


def warehouse_scoring(inputs, static_database, config_dir, cancel_check):
    """前置评分借用冻结角色与匹配，不通过鉴定入口重新打开账号。"""
    orchestrator = warehouse_orchestrator(inputs, static_database, config_dir, cancel_check)
    settings = inputs.values()['matching']
    context = build_equipment_match_context(orchestrator, settings,
                                           cancel_check=cancel_check, batch_defaults=True)
    context = replace(context, settings_identity=matching_settings_identity(settings, scope=MatchingScope.WAREHOUSE))
    scoring = ScoringEngine(config_dir, roles_db=orchestrator.roles_db,
        static_database_path=static_database, stat_catalog=StatCatalog.from_config_dir(config_dir))
    return scoring, context
