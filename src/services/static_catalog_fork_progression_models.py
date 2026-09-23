# 投影弧盘经验材料的只读资料库模型。
"""Fork experience-material DTOs kept outside the main catalog service."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class ForkExperienceMaterialCost:
    item_id: str
    amount: int
    raw_value: str


@dataclass(frozen=True, slots=True)
class ForkExperienceMaterialSource:
    source_row_id: int | None
    row_key: str | None
    content_sha256: str | None
    relative_path: str | None
    source_file_sha256: str | None
    payload_preserved: bool


@dataclass(frozen=True, slots=True)
class ForkExperienceMaterial:
    item_id: str
    name_zh: str | None
    quality: str | None
    icon_path: str | None
    experience_value: int
    costs: tuple[ForkExperienceMaterialCost, ...]
    source: ForkExperienceMaterialSource


def project_fork_experience_materials(
    rows: list[dict[str, Any]],
) -> tuple[ForkExperienceMaterial, ...]:
    return tuple(_project(row) for row in rows)


def _project(row: Mapping[str, Any]) -> ForkExperienceMaterial:
    costs = tuple(
        ForkExperienceMaterialCost(
            item_id=str(cost["cost_item_id"]),
            amount=int(cost["quantity"]),
            raw_value=str(cost["quantity"]),
        )
        for cost in row.get("costs", ())
    )
    return ForkExperienceMaterial(
        item_id=str(row["item_id"]),
        name_zh=row.get("name_zh"),
        quality=row.get("quality"),
        icon_path=row.get("icon_path"),
        experience_value=int(row["experience_value"]),
        costs=costs,
        source=ForkExperienceMaterialSource(
            source_row_id=(
                int(row["source_row_id"])
                if row.get("source_row_id") is not None else None
            ),
            row_key=str(row["row_key"]) if row.get("row_key") is not None else None,
            content_sha256=(
                str(row["content_sha256"])
                if row.get("content_sha256") is not None else None
            ),
            relative_path=(
                str(row["relative_path"])
                if row.get("relative_path") is not None else None
            ),
            source_file_sha256=(
                str(row["source_file_sha256"])
                if row.get("source_file_sha256") is not None else None
            ),
            payload_preserved=bool(row.get("payload_preserved")),
        ),
    )


__all__ = [
    "ForkExperienceMaterial",
    "ForkExperienceMaterialCost",
    "ForkExperienceMaterialSource",
    "project_fork_experience_materials",
]
