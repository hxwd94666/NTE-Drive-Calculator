# 后台一次准备当前角色展示所需的全部直伤数值，保持原服务公式和冻结来源。
from __future__ import annotations

from copy import deepcopy

from src.domain.catalog.static_catalog import StaticCatalogRelease
from src.services.official_role.native_damage import RoleDamageValues, role_damage_calculator
from src.services.official_role.panel.attributes import calculate_official_role_equipment_gain, _context_calculation_items
from src.services.official_role.panel.panel_explanation import calculate_official_role_damage_breakdown
from src.services.official_role.equipment.graduation import graduation_rate
from src.services.official_role.equipment.marginal_scoring import (
    calculate_official_role_margins, calculate_official_role_final_weights,
    calculate_official_role_damage_batch, prefetch_official_role_hidden_equipment_scores,
)
from src.services.official_role.equipment.replacement import calculate_official_role_item_gains


def freeze_role_calculation_detail(detail):
    # 静态大目录已物化且不由编辑控件改写；可编辑组逐组复制，后台不读取Qt对象或追随新草稿。
    frozen = dict(detail)
    frozen.pop('_role_damage_values', None)
    for key in ('profile', 'equipment_contexts', 'equipment_plan', 'property_weights', 'main_property_weights',
                'world_bonus', 'shape_bonus', 'likeability_bonus', 'graduation_template'):
        if key in frozen:
            frozen[key] = deepcopy(frozen[key])
    return frozen


def prepare_role_calculation_values(detail, context_key, *, base_property_weights=None,
                                    base_main_property_weights=None, checkpoint=None):
    """沿既有公共消费者预求值；只返回本次输入的完整数值集合，不保存任何业务事实。"""
    release = detail.get('static_release')
    if not isinstance(release, StaticCatalogRelease) or detail.get('catalog_scope') in {'role_page', 'reference'}:
        return None
    source = {key: value for key, value in detail.items() if key != '_role_damage_values'}
    base = detail.get('property_weights') or {} if base_property_weights is None else base_property_weights
    mains = detail.get('main_property_weights') or {} if base_main_property_weights is None else base_main_property_weights
    with role_damage_calculator(source, checkpoint=checkpoint) as calculator:
        margins = calculate_official_role_margins(source, context_key, calculator=calculator)
        weights = calculate_official_role_final_weights(source, context_key, margins=margins,
            base_property_weights=base, base_main_property_weights=mains, calculator=calculator)
        weighted = {**source, 'property_weights': weights['property_weights'],
                    'main_property_weights': weights['main_property_weights']}
        graduation_rate({**source, 'property_weights': base, 'main_property_weights': mains},
                        context_key, calculator=calculator)
        calculate_official_role_equipment_gain(weighted, context_key, calculator=calculator)
        context = (source.get('equipment_contexts') or {}).get(context_key) or {}
        items = tuple(context.get('items') or ())
        calculate_official_role_item_gains(weighted, context_key, items, calculator=calculator)
        calculate_official_role_damage_breakdown(weighted, context_key, calculator=calculator)
        without_fork = {**weighted, 'profile': {**weighted['profile'], 'fork_id': None}}
        calculate_official_role_damage_batch((weighted, without_fork), context_key, calculator=calculator)
        # 卡片基础评分使用同份最终边际权重；预热既有评分引擎的完整身份键，不复制评分公式。
        prefetch_official_role_hidden_equipment_scores(weighted, _context_calculation_items(context),
            property_weights=weights['property_weights'], main_property_weights=weights['main_property_weights'])
        if checkpoint is not None:
            checkpoint()
        return RoleDamageValues(release, tuple(calculator._values.items()))
