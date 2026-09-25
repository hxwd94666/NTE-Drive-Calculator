# 将原生战报页面响应还原为只读展示对象，不执行公式或补算缺失结果。
from __future__ import annotations

from dataclasses import MISSING, fields, is_dataclass
from functools import lru_cache
import math
from types import UnionType
from typing import Any, Literal, Union, get_args, get_origin, get_type_hints

from src.domain.battle_report import BattleAnalysisSnapshot, BattleDamageComposition, BattleTargetCondition
from src.domain.battle_marginal_benefit import BattleMarginalBenefits
from src.integrations.nte_analysis_core import NativeAnalysisError


@lru_cache(maxsize=None)
def _dataclass_fields(cls):
    hints = get_type_hints(cls, localns={
        'BattleTargetCondition': BattleTargetCondition,
        'BattleDamageComposition': BattleDamageComposition,
    })
    return tuple((field, hints[field.name]) for field in fields(cls))


def decode(value: object, annotation: Any):
    """Read known fields from extensible objects; missing values never become zero."""
    origin, args = get_origin(annotation), get_args(annotation)
    if annotation is Any:
        return value
    if annotation is type(None):
        if value is None:
            return None
        raise NativeAnalysisError('分析核心未知值格式无效')
    if origin in (UnionType, Union):
        for member in args:
            try:
                return decode(value, member)
            except NativeAnalysisError:
                pass
        raise NativeAnalysisError('分析核心字段类型不匹配')
    if origin is Literal:
        if value in args:
            return value
        raise NativeAnalysisError('分析核心状态值无效')
    if annotation is float:
        if type(value) in (int, float) and math.isfinite(value):
            return float(value)
        raise NativeAnalysisError('分析核心数值无效')
    if annotation in (str, int, bool):
        if type(value) is annotation:
            return value
        raise NativeAnalysisError('分析核心字段类型无效')
    if origin is tuple:
        if not isinstance(value, list):
            raise NativeAnalysisError('分析核心列表格式无效')
        if len(args) == 2 and args[1] is Ellipsis:
            return tuple(decode(item, args[0]) for item in value)
        if len(value) != len(args):
            raise NativeAnalysisError('分析核心列表长度无效')
        return tuple(decode(item, typ) for item, typ in zip(value, args))
    if is_dataclass(annotation):
        if not isinstance(value, dict):
            raise NativeAnalysisError('分析核心结果对象无效')
        declared = _dataclass_fields(annotation)
        result = {}
        for field, typ in declared:
            if field.name not in value:
                if field.default is MISSING and field.default_factory is MISSING:
                    raise NativeAnalysisError('分析核心响应缺少必填字段')
                continue
            result[field.name] = decode(value[field.name], typ)
        return annotation(**result)
    raise NativeAnalysisError('分析核心响应类型不受支持')


def decode_page(value: dict):
    from src.services.battle_marginal_panel_service import BattleMarginalPanelResult
    from src.services.battle_report_analysis_load_service import BattleReportAnalysisLoadResult

    required = {'analysis', 'target_catalog', 'target_catalog_error',
                'marginal_benefits', 'marginal_panel', 'candidate_display_analysis'}
    if not isinstance(value, dict) or not required <= value.keys():
        raise NativeAnalysisError('分析核心页面响应字段不匹配')
    catalog = value['target_catalog']
    if catalog is not None and not isinstance(catalog, dict):
        raise NativeAnalysisError('分析核心目标目录无效')
    if value['target_catalog_error'] is not None:
        raise NativeAnalysisError('分析核心目标目录读取失败')
    from src.integrations.native_battle_hit_details_wire import decode_hit_details
    analysis = _decode_snapshot(value['analysis'])
    candidate = _decode_snapshot(value['candidate_display_analysis'])
    return BattleReportAnalysisLoadResult(
        analysis=analysis,
        target_catalog=catalog,
        marginal_benefits=decode(value['marginal_benefits'], BattleMarginalBenefits | None),
        marginal_panel=decode(value['marginal_panel'], BattleMarginalPanelResult | None),
        candidate_display_analysis=candidate,
        hit_details=decode_hit_details(value.get('hit_details'), analysis, candidate),
    )


def _decode_snapshot(value):
    """Restore explicit same-event wire references before typed display decoding."""
    if isinstance(value, dict):
        selected = {hit.get('event_id'): hit.get('native_evidence')
                    for hit in value.get('hits', ()) if isinstance(hit, dict)}
        timeline = []
        for hit in value.get('timeline_hits', ()):
            evidence = hit.get('native_evidence') if isinstance(hit, dict) else None
            if isinstance(evidence, dict) and 'reference_event_id' in evidence:
                identifier = evidence['reference_event_id']
                if ('payload_json' in evidence or not isinstance(identifier, str)
                        or identifier != hit.get('event_id')
                        or not isinstance(selected.get(identifier), dict)
                        or 'payload_json' not in selected[identifier]):
                    raise NativeAnalysisError('分析核心原生证据引用无效')
                hit = {**hit, 'native_evidence': selected[identifier]}
            timeline.append(hit)
        value = {**value, 'timeline_hits': timeline}
    return decode(value, BattleAnalysisSnapshot | None)


def decode_derived_snapshot(value: object, *, battle_record_id: int, dataset_version: str):
    """Validate native persistence metadata without rebuilding its inferred payload."""
    from src.services.battle_inferred_target_condition_service import (
        BattleInferredEncounter,
    )
    if value is None:
        return None
    string_fields = ('algorithm_version', 'static_dataset_id', 'inference_status',
                     'environment_kind', 'environment_ref', 'environment_name', 'source_kind', 'confidence')
    required = {'battle_record_id', 'payload_schema_version', 'static_schema_version',
                'inferred_payload', *string_fields}
    if (not isinstance(value, dict) or not required <= value.keys()
            or any(type(value[key]) is not str for key in string_fields)
            or type(value['battle_record_id']) is not int or value['battle_record_id'] != battle_record_id
            or type(value['payload_schema_version']) is not int or value['payload_schema_version'] != 1
            or type(value['static_schema_version']) is not int or value['static_schema_version'] <= 0
            or value['static_dataset_id'] != dataset_version
            # Algorithm revisions belong to the native producer. Wire compatibility
            # is defined by payload_schema_version, not the legacy Python algorithm.
            or not value['algorithm_version'].strip()
            or value['inference_status'] != 'resolved'):
        raise NativeAnalysisError('分析核心派生快照身份或版本无效')
    inferred = decode(value['inferred_payload'], BattleInferredEncounter)
    if any(getattr(inferred, key) != value[key] for key in (
            'algorithm_version', 'environment_kind', 'environment_ref', 'environment_name',
            'source_kind', 'confidence')):
        raise NativeAnalysisError('分析核心派生快照内容与元数据不匹配')
    return {key: value[key] for key in required}
