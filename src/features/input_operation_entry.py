# 调用组合根显式注入的用户入口说明与环境问题回调。
from typing import Any


def request_input_entry(owner: Any, capability: str, label: str) -> bool:
    callback = getattr(owner, "operation_entry", None)
    return bool(callback(capability, label)) if callable(callback) else False


def show_input_unavailable(owner: Any, label: str, detail: str, target: str = "detection") -> None:
    callback = getattr(owner, "operation_unavailable", None)
    if callable(callback):
        callback(label, detail, target)
