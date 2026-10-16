# 传输固定队友 A 物化和 UID 预留；Python 只验身份，不解释装备公式或拼接半场。
from copy import deepcopy
import json

from src.integrations.axis_team import validate_team_response
from src.integrations.battle_axis_evaluation import validate_axis_selector
from src.integrations.nte_analysis_core import MAX_BYTES, REQUEST_SCHEMA, NativeAnalysisError, _json_object

CAPABILITY = "battle_axis_fixed_team_v1"


def _digest(value):
    return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


def validate_preflight_proof(proof):
    if (not isinstance(proof, dict) or set(proof) != {"source_input_digest", "fixed_build_sha256", "inventory_identities"}
            or not _digest(proof["source_input_digest"]) or not _digest(proof["fixed_build_sha256"])
            or not isinstance(proof["inventory_identities"], list)):
        raise NativeAnalysisError("固定队友预留证明缺失或无效，请重新计算")
    seen = set()
    for item in proof["inventory_identities"]:
        if (not isinstance(item, dict) or set(item) != {"kind", "item_id", "uid_slot", "uid_serial"}
                or item["kind"] not in {"core", "module"} or not isinstance(item["item_id"], str) or not item["item_id"]
                or type(item["uid_slot"]) is not int or item["uid_slot"] < 0
                or type(item["uid_serial"]) is not int or item["uid_serial"] <= 0):
            raise NativeAnalysisError("固定队友库存身份无效")
        key = (item["uid_slot"], item["uid_serial"])
        if key in seen:
            raise NativeAnalysisError("固定队友库存身份重复")
        seen.add(key)


def resolve_fixed_team(client, selector, selected_character_ids, anchor_mode, inventory_identities, *, checkpoint=None):
    client._checkpoint(checkpoint)
    if not client.supports_battle_axis_fixed_team:
        raise NativeAnalysisError("分析组件缺少固定队友预留能力，请更新配套组件")
    try:
        validate_axis_selector(selector)
        selected = list(selected_character_ids)
        if (not 1 <= len(selected) <= 64 or any(type(i) is not int or i <= 0 for i in selected)
                or len(set(selected)) != len(selected) or selector.get("selected_character_id") is not None
                or anchor_mode not in {"original", "effective"}):
            raise ValueError("selection")
        selected.sort()
        available = set()
        identities = []
        for row in inventory_identities:
            kind, slot, serial, item = (row[key] for key in ("kind", "uid_slot", "uid_serial", "item_id"))
            if (kind not in {"core", "module"} or type(slot) is not int or slot < 0
                    or type(serial) is not int or serial <= 0 or not isinstance(item, str) or not item):
                raise ValueError("inventory")
            key = f"nte-{kind}-{slot}-{serial}"
            if key in available:
                raise ValueError("duplicate inventory")
            available.add(key)
            identities.append(dict(kind=kind, uid_slot=slot, uid_serial=serial, item_id=item))
        payload = {"schema_version": REQUEST_SCHEMA, "dataset_version": client.dataset_version,
                   "batch_kind": CAPABILITY, "request": {"version": 1, "battle_request": selector,
                   "selected_character_ids": selected, "anchor_mode": anchor_mode, "inventory_identities": identities}}
        encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
        if len(encoded) > MAX_BYTES:
            raise ValueError("size")
    except (ValueError, TypeError, KeyError) as error:
        raise NativeAnalysisError("固定队友预检输入无效") from error
    result = _json_object(client._run(encoded, checkpoint=checkpoint))
    client._checkpoint(checkpoint)
    validate_team_response(client, result, selector, selected, CAPABILITY)
    try:
        if type(result["fixed_ready"]) is not bool or result["anchor_mode"] != anchor_mode:
            raise ValueError("anchor")
        reserved, members = result["reserved_uids"], result["fixed_members"]
        if (not isinstance(reserved, list) or any(not isinstance(u, str) for u in reserved)
                or reserved != sorted(set(reserved)) or not set(reserved) <= available or not isinstance(members, list)):
            raise ValueError("reserved")
        if result["fixed_ready"]:
            if (not result["ready"] or result["fixed_reason"] is not None or not _digest(result["fixed_build_sha256"])
                    or [m["character_id"] for m in members] != result["fixed_character_ids"]):
                raise ValueError("fixed partition")
            combined = []
            for member in members:
                uids = member["equipment_uids"]
                if (type(member["character_id"]) is not int or not isinstance(uids, list)
                        or not 0 <= len(uids) <= 17 or any(not isinstance(u, str) for u in uids)
                        or uids != sorted(set(uids))):
                    raise ValueError("member equipment")
                combined.extend(uids)
            if len(combined) != len(set(combined)) or sorted(combined) != reserved:
                raise ValueError("fixed uid ownership")
        elif (reserved or members or result["fixed_build_sha256"] is not None
                or not isinstance(result["fixed_reason"], str) or not result["fixed_reason"]):
            raise ValueError("partial fixed proof")
    except (ValueError, TypeError, KeyError) as error:
        raise NativeAnalysisError("固定队友响应与冻结输入不一致") from error
    return deepcopy(result)
