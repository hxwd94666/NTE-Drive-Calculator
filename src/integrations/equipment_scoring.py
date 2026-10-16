# 传输一次冻结评分矩阵，核对来源、维度与有限数；评分和评级公式只在 Rust 执行。
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import asdict
import hashlib
import json
import math
from uuid import uuid4

from src.integrations.nte_analysis_core import MAX_BYTES, REQUEST_SCHEMA, RESPONSE_SCHEMA, NativeAnalysisError, _json_object


def freeze_scoring_catalog(catalog, names):
    """只有目录身份与别名查找，没有数值权重汇总；沿原分配协议的名称顺序。"""
    stats = asdict(catalog)
    stats["tape_main_stat_values"] = stats.pop("tape_main_values")
    stats["tape_main_stats_pool"] = stats.pop("tape_main_stats")
    names = set(names)
    names.update(catalog.gold_base_values)
    names.update(catalog.tape_main_values)
    names.update(catalog.stat_alias_mapping)
    names.update(catalog.stat_alias_mapping.values())
    names.update(name.strip() for name in list(names))
    normalized = {name: catalog.normalize_stat_name(name, is_percent="%" in name) or name.strip() for name in names}
    names.update(normalized.values())
    mapping = {name: {
        "normal": normalized.get(name) or catalog.normalize_stat_name(name, is_percent="%" in name) or name.strip(),
        "main": catalog.normalize_tape_main_stat(name), "alias": catalog.flexible_weight_name(name),
    } for name in sorted(names)}
    return stats, mapping


def freeze_equipment_scoring(catalog, items, profiles, *, queries=(), grade_queries=(),
                             quality_map=None, max_millis=120_000, checkpoint=None):
    """不改原对象；参考值/品质系数按本次目录取原值，由 Rust 计算比例及评分。"""
    quality_map = quality_map if quality_map is not None else {"Gold": 1.0, "Purple": 0.8, "Blue": 0.6}
    frozen, names = [], set(queries)
    for index, item in enumerate(items):
        if checkpoint is not None and index % 64 == 0:
            checkpoint()
        raw = item.model_dump() if hasattr(item, "model_dump") else dict(item)
        sub = list((raw.get("sub_stats") or {}).keys())
        main = raw.get("main_stats") if raw.get("item_type") == "tape" else None
        if main is not None:
            names.add(main)
        names.update(sub)
        frozen.append({"key": f"item-{index}", "item_type": raw["item_type"], "area": raw["area"],
                       "quality_coefficient": quality_map.get(raw["quality"], 1.0), "sub_stats": sub,
                       "main_stat": main, "main_value": raw.get("main_value"),
                       "main_reference_value": catalog.tape_main_values.get(main, 0.0) if main is not None else 0.0})
    profiles = deepcopy(list(profiles))
    for profile in profiles:
        names.update(profile["weights"])
        names.update(profile.get("main_weights") or {})
        names.update(profile.get("zero_weight_stats") or ())
        profile.setdefault("main_weights", None)
        profile.setdefault("max_weight", None)
        profile.setdefault("zero_weight_stats", [])
    stats, mapping = freeze_scoring_catalog(catalog, names)
    inner = {"version": 1, "stats": stats, "names": mapping, "items": frozen, "profiles": profiles,
             "queries": list(queries), "grade_queries": deepcopy(list(grade_queries)), "max_millis": max_millis}
    result = _identified(inner)
    if checkpoint is not None:
        checkpoint()
    return result


def _identified(inner):
    # 权重首个等价名称有意义；摘要保留字典插入顺序，不把不同优先事实压成同一身份。
    encoded = json.dumps(inner, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf8")
    inner["input_identity"] = {"request_id": str(uuid4()), "input_sha256": hashlib.sha256(encoded).hexdigest()}
    return inner


def _error():
    return NativeAnalysisError("装备批量评分协议或冻结输入身份无效")


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def _response(client, result, request):
    keys = [item["key"] for item in request["items"]]
    if (result.get("schema_version") != RESPONSE_SCHEMA or result.get("engine_version") != client.engine_version
            or result.get("dataset_version") != client.dataset_version or result.get("batch_kind") != "equipment_scoring_v1"
            or type(result.get("version")) is not int or result["version"] != 1 or "error" in result
            or result.get("input_identity") != request["input_identity"] or result.get("model_id") != "equipment_base_score_v1"
            or result.get("item_keys") != keys or result.get("queries") != request["queries"]
            or type(result.get("elapsed_millis")) is not int or result["elapsed_millis"] < 0):
        raise _error()
    rows = result.get("profiles")
    if not isinstance(rows, list) or len(rows) != len(request["profiles"]):
        raise _error()
    grades = {"ACE", "SSS", "SS", "S", "A", "B", "C", "D"}
    for row, profile in zip(rows, request["profiles"]):
        if (not isinstance(row, dict) or set(row) != {"key", "max_weight", "scores", "grades", "weights"}
                or row["key"] != profile["key"] or not _number(row["max_weight"])
                or any(not isinstance(row[k], list) for k in ("scores", "grades", "weights"))
                or len(row["scores"]) != len(keys) or len(row["grades"]) != len(keys)
                or len(row["weights"]) != len(request["queries"])
                or any(not _number(v) for v in [*row["scores"], *row["weights"]])
                or any(not isinstance(v, str) or v not in grades for v in row["grades"])):
            raise _error()
    if (not isinstance(result.get("grade_results"), list) or len(result["grade_results"]) != len(request["grade_queries"])
            or any(not isinstance(v, str) or v not in grades for v in result["grade_results"])):
        raise _error()
    return deepcopy(result)


def score_equipment(client, request: Mapping, *, checkpoint=None):
    client._checkpoint(checkpoint)
    if not getattr(client, "supports_equipment_scoring", False):
        raise NativeAnalysisError("分析组件缺少批量基础评分能力，请更新配套组件")
    try:
        payload = json.dumps({"schema_version": REQUEST_SCHEMA, "batch_kind": "equipment_scoring_v1",
                              "dataset_version": client.dataset_version, "request": request},
                             ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf8")
        if len(payload) > MAX_BYTES:
            raise _error()
        frozen = json.loads(payload)["request"]
        result = _response(client, _json_object(client._run(payload, checkpoint=checkpoint)), frozen)
    except (KeyError, TypeError, ValueError, AttributeError, OverflowError):
        raise _error() from None
    client._checkpoint(checkpoint)
    return result
