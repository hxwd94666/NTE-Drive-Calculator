# 在派发前一次冻结轴选择、完整B0与实际装备，结果只读且不调用页面循环或候选持久化。
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import CancelledError
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
from typing import Any
from uuid import uuid4

from src.integrations.battle_axis_evaluation import validate_axis_request
from src.integrations.nte_analysis_core import MAX_BYTES, NativeAnalysisError
from src.services.battle_report.analysis.page_cache import file_identity
from src.services.battle_report.build.equipment import freeze_equipment_context


@dataclass(frozen=True, slots=True)
class FrozenBattleAxisSource:
    owner_identity: tuple
    dataset_version: str
    engine_version: str
    executable: str
    selector_json: str
    resources: tuple
    source_input_digest: str


@dataclass(frozen=True, slots=True)
class FrozenBattleAxisEvaluation:
    source: FrozenBattleAxisSource
    request_json: str


def freeze_actual_axis_equipment(context: Mapping[str, Any], *, checkpoint=None) -> list[dict[str, Any]]:
    """Use original quality/level/main/sub facts, never the role page's full-level scoring projection."""
    items = context.get("items")
    if not isinstance(items, Sequence) or isinstance(items, (str, bytes)):
        raise ValueError("轴评价缺少实际装备集合")
    for index, item in enumerate(items):
        if checkpoint is not None and index % 64 == 0:
            checkpoint()
        if not isinstance(item, Mapping):
            raise ValueError("轴评价包含无效实际装备")
        uid = item.get("uid", item)
        if not isinstance(uid, Mapping):
            raise ValueError("轴评价缺少真实装备身份")
        slot, serial = (uid.get("slot"), uid.get("serial")) if "uid" in item else (uid.get("uid_slot"), uid.get("uid_serial"))
        if type(slot) is not int or slot < 0 or type(serial) is not int or serial <= 0:
            raise ValueError("轴评价缺少真实装备身份")
        if "main_stats" in item or "sub_stats" in item:
            groups = (item.get("main_stats", ()), item.get("sub_stats", ()))
        else:
            groups = (item.get("stats", ()),)
        for stats in groups:
            if not isinstance(stats, Sequence) or isinstance(stats, (str, bytes)):
                raise ValueError("轴评价实际装备词条未知")
            for stat in stats:
                if (not isinstance(stat, Mapping) or type(stat.get("value")) not in (int, float)
                        or not math.isfinite(stat["value"])
                        or type(stat.get("is_percent", stat.get("percent"))) is not bool
                        or not isinstance(stat.get("property_id"), str) or not stat["property_id"].strip()
                        or "stat_group" in stat and stat["stat_group"] not in {"main", "sub"}):
                    raise ValueError("轴评价实际装备词条或单位未知")
    # 公共 helper 有兼容满级计算投影的历史行为；只给原始 items，明确排除 calculation_items。
    # 全仓库入口按批复制，取消不必等到所有装备投影完毕；部分结果仅存在本栈帧。
    frozen = []
    for start in range(0, len(items), 64):
        if checkpoint is not None:
            checkpoint()
        frozen.extend(freeze_equipment_context({"items": items[start:start + 64]}))
    if checkpoint is not None:
        checkpoint()
    return frozen


def _owner(dependencies, semantics_path: Path) -> tuple:
    if dependencies.static_database_path is None:
        raise ValueError("轴评价缺少冻结静态目录")
    return (dependencies.account_id, dependencies.generation,
            str(Path(dependencies.user_database_path).resolve()),
            str(Path(dependencies.static_database_path).resolve()), str(semantics_path.resolve()))


def _resources(client, dependencies, semantics_path: Path) -> tuple:
    # 静态、机制文件和组件是整个作业的输入；账号库按正式战报摘要核对，非相关 WAL/mtime 不作事实。
    paths = (Path(dependencies.static_database_path).resolve(), semantics_path.resolve(), Path(client.executable).resolve())
    return tuple(file_identity(path) for path in paths)


def freeze_axis_source(client, dependencies, semantics_path, context_is_current, request, *, checkpoint=None):
    if client is None or not getattr(client, "supports_battle_page_identity", False):
        raise NativeAnalysisError("战报轴输入身份核对组件未就绪，请更新配套分析组件")
    if (request.marginal_candidate is not None or request.marginal_benefit_candidate is not None
            or request.marginal_drive_units is not None):
        raise ValueError("轴评价只接纳战报选择器，不嵌入页面边际编辑")
    def check():
        if checkpoint is not None:
            checkpoint()
        if not context_is_current(dependencies):
            raise CancelledError
    check()
    owner = _owner(dependencies, semantics_path)
    if request.static_database_path is not None and str(Path(request.static_database_path).resolve()) != owner[3]:
        raise ValueError("轴评价静态上下文已失效")
    selector = {"account_id": owner[0], "generation": owner[1], "user_database_path": owner[2],
        "static_database_path": owner[3], "semantics_path": owner[4], "battle_record_id": request.battle_record_id,
        "start_us": request.start_us, "end_us": request.end_us, "detail_scope": request.detail_scope, "detail_level": "hit"}
    try:
        resources = _resources(client, dependencies, semantics_path)
    except OSError:
        raise NativeAnalysisError("轴评价冻结资源缺失，请核对静态与组件") from None
    # 与成熟页面缓存共用同一定义，不能把这个摘要与轴 facts_sha256 混为同一字段。
    digest = client.load_battle_page_identity(selector, checkpoint=check)
    if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
        raise NativeAnalysisError("战报轴输入身份无效")
    check()
    if _resources(client, dependencies, semantics_path) != resources:
        raise CancelledError
    return FrozenBattleAxisSource(owner, client.dataset_version, client.engine_version,
        str(Path(client.executable).resolve()), json.dumps(selector, ensure_ascii=False, allow_nan=False), resources, digest)


def axis_source_checkpoint(client, dependencies, semantics_path, context_is_current, source, *, checkpoint=None):
    if checkpoint is not None:
        checkpoint()
    if (client is None or not context_is_current(dependencies)
            or _owner(dependencies, semantics_path) != source.owner_identity
            or client.dataset_version != source.dataset_version or client.engine_version != source.engine_version
            or str(Path(client.executable).resolve()) != source.executable):
        raise CancelledError
    try:
        if _resources(client, dependencies, semantics_path) != source.resources:
            raise CancelledError
    except OSError:
        raise CancelledError from None


def verify_axis_source(client, dependencies, semantics_path, context_is_current, source, *, checkpoint=None):
    def check():
        axis_source_checkpoint(client, dependencies, semantics_path, context_is_current, source, checkpoint=checkpoint)
    check()
    if client.load_battle_page_identity(json.loads(source.selector_json), checkpoint=check) != source.source_input_digest:
        raise CancelledError
    check()


def freeze_axis_evaluation(
    client, dependencies, semantics_path: Path, context_is_current, request, *,
    anchor_mode: str, selected_character_id: int, baseline_equipment_by_character: Sequence[Mapping[str, Any]],
    candidates: Sequence[Mapping[str, Any]], checkpoint: Callable[[], None] | None = None,
) -> FrozenBattleAxisEvaluation:
    """Freeze selectors and pointer-free equipment once; no account database or static lookup here."""
    if client is None or not getattr(client, "supports_battle_axis_evaluation", False):
        raise NativeAnalysisError("战报轴批量评价组件未就绪，请更新配套分析组件")
    def check():
        if checkpoint is not None:
            checkpoint()
        if not context_is_current(dependencies):
            raise CancelledError
        client._checkpoint(None)
    check()
    if (request.marginal_candidate is not None or request.marginal_benefit_candidate is not None
            or request.marginal_drive_units is not None):
        raise ValueError("轴评价只接纳战报选择器，不嵌入页面边际编辑")
    if (not isinstance(baseline_equipment_by_character, Sequence) or isinstance(baseline_equipment_by_character, (str, bytes))
            or not 1 <= len(baseline_equipment_by_character) <= 16
            or not isinstance(candidates, Sequence) or isinstance(candidates, (str, bytes)) or len(candidates) > 256):
        raise ValueError("轴评价角色或候选批次超过冻结边界")
    source = freeze_axis_source(client, dependencies, semantics_path, context_is_current, request, checkpoint=checkpoint)
    if request.selected_character_id is not None and request.selected_character_id != selected_character_id:
        raise ValueError("轴评价角色上下文已失效")
    payload = {"version": 1, "battle_request": json.loads(source.selector_json),
        "anchor_mode": anchor_mode, "selected_character_id": selected_character_id, "equipment_value_mode": "actual_level",
        "baseline_equipment_by_character": [], "candidates": []}
    for build in baseline_equipment_by_character:
        check()
        payload["baseline_equipment_by_character"].append({"character_id": build["character_id"],
            "equipment": freeze_actual_axis_equipment({"items": build["equipment"]}, checkpoint=check)})
    for candidate in candidates:
        check()
        payload["candidates"].append({"candidate_id": candidate["candidate_id"],
            "equipment": freeze_actual_axis_equipment({"items": candidate["equipment"]}, checkpoint=check)})
    # JSON 字节持有深冻结输入；UI 对原始映射的后续编辑不会改变已派发作业。
    encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))
    payload["input_identity"] = {"request_id": str(uuid4()), "input_sha256": hashlib.sha256(encoded.encode("utf8")).hexdigest()}
    validate_axis_request(payload)
    encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    if len(encoded.encode("utf8")) > MAX_BYTES:
        raise NativeAnalysisError("轴评价冻结输入超过大小限制")
    check()
    axis_source_checkpoint(client, dependencies, semantics_path, context_is_current, source, checkpoint=checkpoint)
    return FrozenBattleAxisEvaluation(source, encoded)


def evaluate_frozen_axis(
    client, dependencies, semantics_path: Path, context_is_current, frozen: FrozenBattleAxisEvaluation, *,
    checkpoint: Callable[[], None] | None = None,
) -> dict[str, Any]:
    def check():
        axis_source_checkpoint(client, dependencies, semantics_path, context_is_current, frozen.source, checkpoint=checkpoint)
    verify_axis_source(client, dependencies, semantics_path, context_is_current, frozen.source, checkpoint=checkpoint)
    result = client.evaluate_battle_axis(json.loads(frozen.request_json), checkpoint=check)
    if result["source_input_digest"] != frozen.source.source_input_digest:
        raise CancelledError
    verify_axis_source(client, dependencies, semantics_path, context_is_current, frozen.source, checkpoint=checkpoint)
    # 不写编辑副本、推断快照、配装或采集事实；P4 接受/保存由单独冻结预览门禁负责。
    return result
