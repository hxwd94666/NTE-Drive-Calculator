# 定义 nte-core 错误类型、领域错误码和库存协议校验。
"""Protocol types and inventory DTO validation for nte-core."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


JsonObject = dict[str, Any]
MODS_PLUGIN_UNAVAILABLE_CODES = frozenset(
    {"MODS_PLUGIN_UNAVAILABLE", "EQUIPMENT_PLUGIN_UNAVAILABLE"}
)
MODS_PLUGIN_BUSY_CODES = frozenset(
    {"MODS_PLUGIN_BUSY", "EQUIPMENT_PLUGIN_BUSY"}
)


class NteCoreError(RuntimeError):
    """Base nte-core integration error."""


class NteCoreNotFoundError(NteCoreError):
    """Raised when nte-core.exe cannot be resolved."""


class NteCoreProcessError(NteCoreError):
    def __init__(
        self,
        message: str,
        *,
        return_code: int | None = None,
        stderr_lines: Sequence[str] = (),
    ) -> None:
        super().__init__(message)
        self.return_code = return_code
        self.stderr_lines = tuple(stderr_lines)


class NteCoreProtocolError(NteCoreError):
    """Raised when stdout violates the JSON-RPC/NDJSON contract."""


class NteCoreTimeoutError(NteCoreError):
    def __init__(self, method: str, timeout: float) -> None:
        super().__init__(
            f"nte-core request timed out: {method} ({timeout:.1f}s)"
        )
        self.method = method
        self.timeout = timeout


class NteCoreRpcError(NteCoreError):
    def __init__(self, error: Mapping[str, Any]) -> None:
        self.code = int(error.get("code", -32603))
        self.message = str(error.get("message", "Core error"))
        data = error.get("data")
        self.data = dict(data) if isinstance(data, Mapping) else {}
        domain_code = self.data.get("domain_code")
        self.domain_code = (
            str(domain_code) if domain_code is not None else None
        )
        suffix = f" [{self.domain_code}]" if self.domain_code else ""
        super().__init__(
            f"nte-core RPC error {self.code}{suffix}: {self.message}"
        )


def nte_core_error_has_domain_code(
    error: object, codes: frozenset[str]
) -> bool:
    domain_code = getattr(error, "domain_code", None)
    if isinstance(domain_code, str):
        return domain_code in codes
    message = str(error)
    return any(f"[{code}]" in message for code in codes)


def is_mods_plugin_unavailable_error(error: object) -> bool:
    return nte_core_error_has_domain_code(
        error, MODS_PLUGIN_UNAVAILABLE_CODES
    )


def is_mods_plugin_busy_error(error: object) -> bool:
    return nte_core_error_has_domain_code(error, MODS_PLUGIN_BUSY_CODES)


def equipment_request_failure_kind(error: object) -> str:
    """Return a stable UI/report category without discarding the original error."""

    if is_mods_plugin_busy_error(error):
        return "plugin_busy"
    if is_mods_plugin_unavailable_error(error):
        return "plugin_unavailable"
    if isinstance(error, NteCoreTimeoutError):
        return "core_request_timeout"
    if getattr(error, "domain_code", None) == "EQUIPMENT_REQUEST_REJECTED":
        return "request_rejected"
    return "apply_error"


def inventory_item_placement(
    item: Mapping[str, Any],
) -> tuple[int, int] | None:
    placement = item.get("equipped_placement")
    if placement is None:
        return None
    if not isinstance(placement, Mapping):
        raise NteCoreProtocolError(
            "inventory equipped_placement must be an object or null"
        )
    row = placement.get("row")
    column = placement.get("column")
    if (
        isinstance(row, bool)
        or not isinstance(row, int)
        or isinstance(column, bool)
        or not isinstance(column, int)
        or not 1 <= row <= 5
        or not 1 <= column <= 5
    ):
        raise NteCoreProtocolError(
            "inventory equipped_placement row and column "
            "must be integers in 1..5"
        )
    return row, column


def group_inventory_items_by_character(
    snapshot: Mapping[str, Any],
) -> dict[int, list[JsonObject]]:
    items = snapshot.get("items")
    if not isinstance(items, list):
        raise NteCoreProtocolError("inventory snapshot items must be an array")
    grouped: dict[int, list[JsonObject]] = {}
    for item in items:
        if not isinstance(item, Mapping):
            raise NteCoreProtocolError(
                "inventory snapshot item must be an object"
            )
        inventory_item_placement(item)
        character_id = item.get("equipped_character_id")
        if character_id is None:
            continue
        if (
            isinstance(character_id, bool)
            or not isinstance(character_id, int)
            or character_id <= 0
        ):
            raise NteCoreProtocolError(
                "inventory equipped_character_id must be "
                "a positive integer or null"
            )
        grouped.setdefault(character_id, []).append(dict(item))
    return grouped
