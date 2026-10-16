# 传输冻结图纸输入并核对 Rust 输出身份；不在 Python 枚举组合、摆盘或选择图纸。
from collections import Counter
from concurrent.futures import CancelledError
import json

from src.integrations.nte_analysis_core import (
    MAX_BYTES, REQUEST_SCHEMA, RESPONSE_SCHEMA, NativeAnalysisError, NativeAnalysisCancelled, _json_object,
)


CAPABILITY = "allocation_blueprints_v1"
STOP_REASONS = frozenset({"node_budget", "time_budget", "blueprint_budget",
                          "combination_budget", "output_budget"})
DEFAULT_BUDGET = {"max_nodes": 10_000_000, "max_blueprints": 100_000, "max_millis": 120_000,
                  "max_combinations": 100_000, "max_output_bytes": 32 * 1024 * 1024}


def create_blueprint_executor(client, *, cancel_check=None):
    """绑定本次分析组件；基础分配只接收完整图纸，不回退 Python 或缓存截断结果。"""
    def checkpoint():
        if cancel_check is not None and cancel_check():
            raise CancelledError("分配图纸生成已取消")

    checkpoint()
    if client is None or not client.supports_blueprints:
        raise NativeAnalysisError("分析组件缺少 Rust 图纸生成能力，请更新配套组件")

    def execute(request):
        checkpoint()
        frozen = {**request, "budget": dict(request.get("budget") or DEFAULT_BUDGET)}
        try:
            result = client.generate_blueprints(frozen, checkpoint=checkpoint)
        except NativeAnalysisCancelled:
            raise CancelledError("分配图纸生成已取消") from None
        checkpoint()
        if not result["complete"]:
            raise NativeAnalysisError("图纸生成达到搜索预算，尚未完成全部候选；请减少参与角色或调整套装后重试")
        return result["blueprints"]

    return execute


def _verify_plans(result, request):
    roles = {role["role"]: role for role in request["roles"]}
    shapes = {shape["shape_id"]: shape for shape in request["shapes"]}
    plans = result["blueprints"]
    if not isinstance(plans, dict) or set(plans) != set(roles):
        raise ValueError("role identity")
    count = 0
    for name, rows in plans.items():
        if not isinstance(rows, list):
            raise ValueError("plans")
        role = roles[name]
        for row in rows:
            count += 1
            if not isinstance(row, dict) or row["set_effect_mode"] != role["set_effect_mode"]:
                raise ValueError("set mode")
            required, extra = row["set_pieces"], row["extra_pieces"]
            if any(not isinstance(group, list) or any(
                    not isinstance(key, str) or key not in shapes or key == "TAPE_15"
                    for key in group) for group in (required, extra)):
                raise ValueError("shape identity")
            expected_set = Counter(role["set_shapes"])
            actual_set = Counter(required)
            if (role["set_effect_mode"] == "none" and required
                    or role["set_effect_mode"] == "four_piece" and actual_set != expected_set
                    or role["set_effect_mode"] == "two_piece" and (
                        len(required) != min(2, len(role["set_shapes"])) or actual_set - expected_set)):
                raise ValueError("required pieces")
            board, frozen = row["board"], role["board"]
            if not isinstance(board, list) or len(board) != len(frozen):
                raise ValueError("board identity")
            actual_cells = Counter()
            for output_row, source_row in zip(board, frozen):
                if not isinstance(output_row, list) or len(output_row) != len(source_row):
                    raise ValueError("board dimensions")
                for cell, source in zip(output_row, source_row):
                    if source != 0:
                        if type(cell) is not int or cell != source:
                            raise ValueError("blocked cell")
                    elif not isinstance(cell, str) or cell not in shapes:
                        raise ValueError("incomplete board")
                    else:
                        actual_cells[cell] += 1
            expected_cells = {key: amount * shapes[key]["area"]
                              for key, amount in Counter(required + extra).items()}
            if actual_cells != expected_cells:
                raise ValueError("piece cells")
    if result["generated"] != count:
        raise ValueError("generated count")


def generate_blueprints(client, request, *, checkpoint=None):
    """返回带覆盖标记的整批结果；调用方须明确处理截断，不把它缓存为完整结果。"""
    client._checkpoint(checkpoint)
    if not client.supports_blueprints:
        raise NativeAnalysisError("分析组件缺少 Rust 图纸生成能力，请更新配套组件")
    payload = {"schema_version": REQUEST_SCHEMA, "dataset_version": client.dataset_version,
               "batch_kind": CAPABILITY, "request": request}
    encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
    if len(encoded) > MAX_BYTES:
        raise NativeAnalysisError("图纸输入超过大小限制")
    # 验收只比较真正派发的值；调用方在等待期间编辑草稿不应重新解释响应。
    request = json.loads(encoded)["request"]
    result = _json_object(client._run(encoded, checkpoint=checkpoint))
    client._checkpoint(checkpoint)
    try:
        if (result["schema_version"] != RESPONSE_SCHEMA or result["engine_version"] != client.engine_version
                or result["dataset_version"] != client.dataset_version or result["batch_kind"] != CAPABILITY
                or type(result["version"]) is not int or result["version"] != 1
                or type(result["complete"]) is not bool
                or result["termination"] not in STOP_REASONS | {"complete"}
                or result["complete"] != (result["termination"] == "complete")):
            raise ValueError("response identity or completion")
        budget = request["budget"]
        for field, limit in (("nodes", budget["max_nodes"]), ("generated", budget["max_blueprints"]),
                             ("combinations", budget.get("max_combinations", 100_000)),
                             ("output_bytes", budget.get("max_output_bytes", 32 * 1024 * 1024))):
            if type(result[field]) is not int or not 0 <= result[field] <= limit:
                raise ValueError("budget counters")
        _verify_plans(result, request)
    except (KeyError, TypeError, ValueError, AttributeError) as error:
        raise NativeAnalysisError("图纸响应与冻结身份、布局或预算不一致") from error
    client._checkpoint(checkpoint)
    return result
