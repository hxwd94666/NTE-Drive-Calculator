# 为一次加权分配预览冻结包含弧盘属性的官方角色动态权重。
"""Freeze formula-derived official role weights for one allocation preview."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping

from src.services.allocation_context import AllocationContext
from src.services.official_role_page_service import load_official_role_detail
from src.services.official_role_scoring_service import calculate_official_role_final_weights


def freeze_official_role_final_weights(
    context: AllocationContext,
    *,
    user_database_path: Path,
    shared_database_path: Path | None,
    static_database_path: Path | None,
) -> tuple[AllocationContext, dict[int, Mapping[str, Any]]]:
    """Freeze official panel margins before any allocation strategy runs.

    Durable account weights are replaced only in the immutable preview.  The
    margin calculation includes the selected fork's unconditional refinement
    property; custom roles keep their account-owned weights unchanged.
    """

    frozen_roles = []
    details: dict[int, Mapping[str, Any]] = {}
    for role in context.roles:
        try:
            detail = load_official_role_detail(
                user_database_path,
                role.character_id,
                include_inventory_contexts=False,
                static_database_path=static_database_path,
                static_schema_version=context.static_dataset.schema_version,
                shared_database_path=shared_database_path,
            )
        except (OSError, ValueError):
            frozen_roles.append(role)
            continue
        final = calculate_official_role_final_weights(
            detail,
            "current",
            base_property_weights=dict(role.effective_property_weights),
            base_main_property_weights=dict(role.effective_main_property_weights),
        )
        frozen_roles.append(replace(
            role,
            effective_property_weights=tuple(sorted(
                (str(property_id), float(weight))
                for property_id, weight in final["property_weights"].items()
            )),
            effective_main_property_weights=tuple(sorted(
                (str(property_id), float(weight))
                for property_id, weight in final["main_property_weights"].items()
            )),
        ))
        details[role.character_id] = detail
    return replace(context, roles=tuple(frozen_roles)), details
