# 校验养成历史的轻量结果快照与输入角色身份一致性。
from __future__ import annotations

from typing import Any

from src.domain.cultivation_history import (
    PAYLOAD_VERSION, boolean, integer, object_fields, rows, text_value, unique, utc_time,
)


def _stamina_values(value: dict[str, Any]) -> None:
    if text_value(value["status"]) not in {"complete", "partial", "unavailable"}:
        raise ValueError("养成历史结果状态无效")
    known = integer(value["known_stamina"])
    total = value["total_stamina"]
    if total is not None:
        integer(total, known)


def normalize_snapshot(value: object, configuration: dict[str, Any]) -> dict[str, Any]:
    data = object_fields(value, "version dataset algorithm_version materials target_summaries stamina gaps")
    if integer(data["version"]) != PAYLOAD_VERSION:
        raise ValueError("养成历史结果版本无效")
    dataset = object_fields(data["dataset"], "dataset_id schema_version importer_version built_at_utc")
    text_value(dataset["dataset_id"], maximum=256)
    integer(dataset["schema_version"], 1)
    # importer 使用独立版本标签，不与 schema 整数混作同一种身份。
    text_value(dataset["importer_version"], maximum=128)
    if utc_time(dataset["built_at_utc"]) is None:
        raise ValueError("养成历史资料缺少构建时间")
    data["dataset"] = dataset
    text_value(data["algorithm_version"], maximum=128)
    materials = []
    for raw in rows(data["materials"]):
        item = object_fields(raw, "item_id name required allocated_equivalent remaining stamina_eligible")
        text_value(item["item_id"], maximum=256)
        text_value(item["name"])
        required = integer(item["required"])
        allocated = integer(item["allocated_equivalent"], 0, required)
        if integer(item["remaining"], 0, required) + allocated != required:
            raise ValueError("养成历史材料合计不一致")
        boolean(item["stamina_eligible"])
        materials.append(item)
    unique(materials, "item_id")
    data["materials"] = materials
    targets = {target["line_id"]: target["character_id"] for target in configuration["targets"]}
    summaries = []
    for raw in rows(data["target_summaries"]):
        summary = object_fields(raw, "line_id character_id status known_stamina total_stamina")
        text_value(summary["line_id"], maximum=256)
        integer(summary["character_id"], 1)
        if targets.get(summary["line_id"]) != summary["character_id"]:
            raise ValueError("养成历史输入和结果角色不一致")
        _stamina_values(summary)
        summaries.append(summary)
    unique(summaries, "line_id")
    if len(summaries) != len(targets):
        raise ValueError("养成历史结果角色不完整")
    data["target_summaries"] = summaries
    stamina = object_fields(data["stamina"], "status known_stamina total_stamina runs")
    _stamina_values(stamina)
    if (stamina["status"] == "complete") != (stamina["total_stamina"] is not None):
        raise ValueError("养成历史完整体力和状态不一致")
    runs = []
    for raw in rows(stamina["runs"]):
        run = object_fields(raw, "stage_id label runs stamina_per_run total_stamina source")
        text_value(run["stage_id"], maximum=256)
        text_value(run["label"])
        integer(run["runs"], 1)
        integer(run["stamina_per_run"], 1)
        if integer(run["total_stamina"]) != run["runs"] * run["stamina_per_run"]:
            raise ValueError("养成历史副本次数与体力不一致")
        text_value(run["source"], maximum=128)
        runs.append(run)
    unique(runs, "stage_id")
    if sum(run["total_stamina"] for run in runs) != stamina["known_stamina"]:
        raise ValueError("养成历史副本合计与已知体力不一致")
    if stamina["total_stamina"] is not None and stamina["total_stamina"] != stamina["known_stamina"]:
        raise ValueError("养成历史完整体力与已知体力不一致")
    stamina["runs"] = runs
    data["stamina"] = stamina
    gaps = []
    for raw in rows(data["gaps"]):
        gap = object_fields(raw, "line_id reason_code item_id")
        if gap["line_id"] is not None:
            text_value(gap["line_id"], maximum=128)
            if gap["line_id"] not in targets:
                raise ValueError("养成历史缺口角色身份无效")
        text_value(gap["reason_code"])
        if gap["item_id"] is not None:
            text_value(gap["item_id"], maximum=256)
        gaps.append(gap)
    data["gaps"] = gaps
    return data
