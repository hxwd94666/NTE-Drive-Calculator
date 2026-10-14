# 定义养成历史的有界显式数据格式和不可变持久化投影。
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from src.domain.progression.stamina import project_identification_level

PAYLOAD_VERSION = 1
MAX_PAYLOAD_BYTES = 4 * 1024 * 1024
MAX_COLLECTION_SIZE = 100_000


class HistoryConflict(ValueError):
    """选定记录已改变或删除，调用方必须刷新而不是补插。"""


class HistoryContextExpired(RuntimeError):
    """冻结账号、资料或草稿已经失效。"""


def object_fields(value: object, fields: str) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != set(fields.split()):
        raise ValueError("养成历史对象字段或版本不匹配")
    return dict(value)


def text_value(value: object, *, maximum: int = 512) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= maximum:
        raise ValueError("养成历史文本或身份无效")
    if any(ord(char) < 32 for char in value):
        raise ValueError("养成历史文本包含控制字符")
    return value


def integer(value: object, minimum: int = 0, maximum: int = 2**63 - 1) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError("养成历史数值类型或范围无效")
    return value


def boolean(value: object) -> bool:
    if type(value) is not bool:
        raise ValueError("养成历史开关必须为布尔值")
    return value


def rows(value: object) -> list[Any]:
    if not isinstance(value, (list, tuple)) or len(value) > MAX_COLLECTION_SIZE:
        raise ValueError("养成历史集合格式或规模无效")
    return list(value)


def utc_time(value: object) -> str | None:
    if value is None:
        return None
    raw = text_value(value, maximum=64)
    try:
        moment = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("养成历史观测时间无效") from error
    if moment.utcoffset() is None or moment.utcoffset().total_seconds() != 0:
        raise ValueError("养成历史观测时间必须为 UTC")
    return raw


def unique(values: list[dict[str, Any]], field: str) -> None:
    if len({row[field] for row in values}) != len(values):
        raise ValueError(f"养成历史包含重复 {field}")


def _state(value: dict[str, Any]) -> None:
    for prefix in ("current", "target"):
        level = integer(value[f"{prefix}_level"], 1, 80)
        stage = integer(value[f"{prefix}_stage"], 0, 6)
        minimum = 1 if stage == 0 else (stage + 1) * 10
        if not minimum <= level <= (stage + 2) * 10:
            raise ValueError("养成历史等级和突破阶段不匹配")
    # 已禁用模块仍保留编辑状态；目标区间可行性由求解阶段判断。


def normalize_configuration(value: object) -> dict[str, Any]:
    data = object_fields(value, "version mode targets hunter_level identification_level material_scope owned_materials")
    if integer(data["version"]) != PAYLOAD_VERSION or text_value(data["mode"]) not in {"single", "batch"}:
        raise ValueError("养成历史配置版本或模式无效")
    if text_value(data["material_scope"]) not in {"all", "stamina"}:
        raise ValueError("养成历史材料范围无效")
    integer(data["hunter_level"], 1, 60)
    identification = data["identification_level"]
    if identification is not None:
        integer(identification, 0, 7)
    targets = []
    for raw in rows(data["targets"]):
        target = object_fields(raw, "line_id character_id name current_level current_stage target_level target_stage character_enabled skills_enabled skills fork")
        text_value(target["line_id"], maximum=256)
        integer(target["character_id"], 1)
        text_value(target["name"])
        _state(target)
        boolean(target["character_enabled"])
        boolean(target["skills_enabled"])
        skills = []
        for raw_skill in rows(target["skills"]):
            skill = object_fields(raw_skill, "skill_id current_level target_level")
            text_value(skill["skill_id"], maximum=256)
            integer(skill["current_level"], 1, 10)
            integer(skill["target_level"], 1, 10)
            skills.append(skill)
        unique(skills, "skill_id")
        target["skills"] = skills
        if target["fork"] is not None:
            fork = object_fields(target["fork"], "fork_id name enabled current_level current_stage target_level target_stage")
            text_value(fork["fork_id"], maximum=256)
            text_value(fork["name"])
            boolean(fork["enabled"])
            _state(fork)
            target["fork"] = fork
        targets.append(target)
    if not targets or (data["mode"] == "single" and len(targets) != 1):
        raise ValueError("养成历史角色集合与模式不匹配")
    unique(targets, "line_id")
    unique(targets, "character_id")
    data["targets"] = targets
    data["owned_materials"] = normalize_owned_materials(data["owned_materials"])
    return data


def normalize_owned_materials(value: object) -> list[dict[str, Any]]:
    owned = []
    for raw in rows(value):
        item = object_fields(raw, "item_id quantity manual_override source observed_at_utc")
        text_value(item["item_id"], maximum=256)
        integer(item["quantity"], 0, 99_999_999)
        boolean(item["manual_override"])
        if text_value(item["source"]) not in {"manual", "native", "packet"}:
            raise ValueError("养成历史材料来源无效")
        utc_time(item["observed_at_utc"])
        if item["manual_override"] and item["source"] != "manual":
            raise ValueError("养成历史手工覆盖来源不一致")
        owned.append(item)
    unique(owned, "item_id")
    return sorted(owned, key=lambda item: item["item_id"])


# 输入与结果同属一种历史格式；在同一入口核对角色身份和数量，避免校验规则互相导入。
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


def _bounded_tree(value: object, depth: int = 0) -> None:
    if depth > 12:
        raise ValueError("养成历史嵌套过深")
    if isinstance(value, Mapping):
        if len(value) > MAX_COLLECTION_SIZE:
            raise ValueError("养成历史对象规模超限")
        for key, child in value.items():
            text_value(key, maximum=128)
            _bounded_tree(child, depth + 1)
    elif isinstance(value, (list, tuple)):
        for child in rows(value):
            _bounded_tree(child, depth + 1)
    elif value is not None and type(value) not in {str, int, bool}:
        raise ValueError("养成历史包含不支持的数值或对象")


def _encoded(value: object) -> str:
    _bounded_tree(value)
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    if len(raw.encode("utf-8")) > MAX_PAYLOAD_BYTES:
        raise ValueError("养成历史超出单条大小预算，计算结果保留")
    return raw


def _no_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("养成历史 JSON 包含重复字段")
        result[key] = value
    return result


def decode_summary(raw: str) -> dict[str, Any]:
    """列表只解码有界摘要；损坏行不影响其正式身份和删除能力。"""
    if not isinstance(raw, str) or len(raw.encode("utf-8")) > MAX_PAYLOAD_BYTES:
        raise ValueError("养成历史摘要格式或大小无效")
    try:
        value = json.loads(raw, object_pairs_hook=_no_duplicate_keys)
        _bounded_tree(value)
        data = object_fields(value, "version characters status known_stamina total_stamina gap_count")
        if integer(data["version"]) != PAYLOAD_VERSION:
            raise ValueError("养成历史摘要版本暂未支持")
        characters = []
        for row in rows(data["characters"]):
            character = object_fields(row, "character_id name target_label")
            integer(character["character_id"], 1)
            text_value(character["name"])
            text_value(character["target_label"])
            characters.append(character)
        if not characters:
            raise ValueError("养成历史摘要缺少角色")
        unique(characters, "character_id")
        data["characters"] = characters
        if text_value(data["status"]) not in {"complete", "partial", "unavailable"}:
            raise ValueError("养成历史摘要状态无效")
        integer(data["known_stamina"])
        integer(data["gap_count"], 0, MAX_COLLECTION_SIZE)
        total = data["total_stamina"]
        if total is not None:
            integer(total)
        if ((data["status"] == "complete") != (total is not None)
                or total is not None and total != data["known_stamina"]):
            raise ValueError("养成历史摘要体力状态不一致")
        return data
    except (RecursionError, TypeError, json.JSONDecodeError) as error:
        raise ValueError("养成历史摘要解析失败") from error


def freeze_configuration(value: object) -> str:
    """在提交计算之前复制并校验完整草稿，之后不再读取活控件。"""
    _encoded(value)
    configuration = normalize_configuration(value)
    project_identification_level(configuration["hunter_level"], effective_level=configuration["identification_level"])
    return _encoded(configuration)


def _configuration_projection(raw: str) -> dict[str, Any]:
    """有界读取配置供只读名称投影使用，不消费当前角色资料。"""
    if not isinstance(raw, str) or len(raw.encode("utf-8")) > MAX_PAYLOAD_BYTES:
        raise ValueError("历史配置大小或格式无效")
    try:
        value = json.loads(raw, object_pairs_hook=_no_duplicate_keys)
        _bounded_tree(value)
        return normalize_configuration(value)
    except (RecursionError, TypeError, json.JSONDecodeError) as error:
        raise ValueError("历史配置名称解析失败") from error


def configuration_character_labels(raw: str) -> tuple[str, ...]:
    """从本页历史的原配置投影名称，不改持久化摘要或消费当前资料。"""
    config = _configuration_projection(raw)
    return tuple(target["name"] + (f"（{target['fork']['name']}）" if target["fork"] else "")
                 for target in config["targets"])


def configuration_search_names(raw: str) -> tuple[str, ...]:
    """角色与选定弧盘分别匹配，未参与养成的弧盘也保留其名称。"""
    config = _configuration_projection(raw)
    names = []
    for target in config["targets"]:
        names.append(target["name"])
        if target["fork"] is not None:
            names.append(target["fork"]["name"])
    return tuple(names)


@dataclass(frozen=True, slots=True)
class HistoryCalculationEnvelope:
    account_id: str
    context_identity: object
    session_id: str
    request_revision: int
    write_epoch: int
    configuration_json: str
    explicit_calculation: bool


def _target_label(target: dict[str, Any]) -> str:
    parts = []
    if target["character_enabled"]:
        parts.append(f"人物 {target['current_level']}/{target['current_stage']}→{target['target_level']}/{target['target_stage']}")
    if target["skills_enabled"] and target["skills"]:
        levels = [skill["target_level"] for skill in target["skills"]]
        parts.append(f"技能 {len(levels)} 项，目标 {min(levels)}–{max(levels)} 级")
    fork = target["fork"]
    if fork is not None and fork["enabled"]:
        parts.append(f"弧盘 {fork['current_level']}/{fork['current_stage']}→{fork['target_level']}/{fork['target_stage']}")
    return "；".join(parts) or "未启用养成模块"


@dataclass(frozen=True, slots=True)
class HistoryPayload:
    """持久化边界只传不可变字符串，不携带控件、路径或活对象。"""

    mode: str
    configuration_json: str
    result_snapshot_json: str
    summary_json: str
    content_sha256: str

    @classmethod
    def create(cls, configuration: object, result: object) -> HistoryPayload:
        # 先检查嵌套与大小预算，再规范化调用方结构，失败保留计算结果。
        _encoded(configuration)
        _encoded(result)
        config = normalize_configuration(configuration)
        snapshot = normalize_snapshot(result, config)
        config_raw, snapshot_raw = _encoded(config), _encoded(snapshot)
        if len(config_raw.encode("utf-8")) + len(snapshot_raw.encode("utf-8")) > MAX_PAYLOAD_BYTES:
            raise ValueError("养成历史超出单条大小预算，计算结果保留")
        summary = {
            "version": PAYLOAD_VERSION,
            "characters": [{"character_id": target["character_id"], "name": target["name"],
                            "target_label": _target_label(target)}
                           for target in config["targets"]],
            "status": snapshot["stamina"]["status"],
            "known_stamina": snapshot["stamina"]["known_stamina"],
            "total_stamina": snapshot["stamina"]["total_stamina"],
            "gap_count": len(snapshot["gaps"]),
        }
        fingerprint = hashlib.sha256((config_raw + "\n" + snapshot_raw).encode("utf-8")).hexdigest()
        return cls(config["mode"], config_raw, snapshot_raw, _encoded(summary), fingerprint)

    @classmethod
    def decode(cls, configuration_json: str, result_snapshot_json: str) -> HistoryPayload:
        if (not isinstance(configuration_json, str) or not isinstance(result_snapshot_json, str)
                or len(configuration_json.encode("utf-8")) + len(result_snapshot_json.encode("utf-8")) > MAX_PAYLOAD_BYTES):
            raise ValueError("养成历史正文格式或大小无效")
        try:
            config = json.loads(configuration_json, object_pairs_hook=_no_duplicate_keys)
            snapshot = json.loads(result_snapshot_json, object_pairs_hook=_no_duplicate_keys)
            return cls.create(config, snapshot)
        except (RecursionError, TypeError, json.JSONDecodeError) as error:
            raise ValueError("养成历史正文解析失败") from error


@dataclass(frozen=True, slots=True)
class HistorySelection:
    history_id: str
    revision: int


@dataclass(frozen=True, slots=True)
class HistorySummary:
    history_id: str
    mode: str
    revision: int
    first_calculated_at_utc: str
    last_calculated_at_utc: str
    summary_json: str
    # 仅供当前页读取的名称投影，不序列化，也不参与身份或删除判断。
    character_labels: tuple[str, ...] = field(default=(), compare=False)


@dataclass(frozen=True, slots=True)
class HistoryRecord:
    summary: HistorySummary
    payload: HistoryPayload


@dataclass(frozen=True, slots=True)
class HistoryPage:
    items: tuple[HistorySummary, ...]
    total: int
    page: int
    page_size: int
