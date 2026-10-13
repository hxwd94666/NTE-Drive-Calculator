# 校验独立 reference 图鉴的正式养成货币、付费副本与方斯例外边界。
"""Stable build and promotion gate for reference-catalog currency identity."""

from __future__ import annotations

import sqlite3


_COST_COLUMNS = (
    ("character_breakthrough_cost", "item_id"),
    ("character_exp_material_cost", "cost_item_id"),
    ("fork_exp_material_cost", "cost_item_id"),
)


def validate_reference_progression_currency(
    connection: sqlite3.Connection,
) -> dict[str, int | str]:
    """拒绝货币回归，不固定发行记录数。Trailclone_gold 与 drop_fons1 是正式来源身份；来源变化须显式审查，禁止 importer 静默改名。"""

    names = dict(connection.execute(
        "SELECT item_id, name_zh FROM progression_item WHERE item_id IN ('Gold','Fons')"
    ))
    if names != {"Gold": "甲硬币", "Fons": "方斯"}:
        raise ValueError("progression_item 的 Gold/Fons 正式名称不匹配")
    alias = connection.execute(
        "SELECT item_id FROM progression_item_alias "
        "WHERE token='gold' AND context='progression_cost'"
    ).fetchall()
    if alias != [("Gold",)]:
        raise ValueError("progression_item_alias 的 gold 成本必须精确指向 Gold")

    counts: dict[str, int | str] = {"cost_alias": "Gold"}
    for table, column in _COST_COLUMNS:
        rows = dict(connection.execute(
            f"SELECT {column},COUNT(*) FROM {table} "
            f"WHERE {column} IN ('Gold','Fons') GROUP BY {column}"
        ))
        if rows.get("Fons", 0) or not rows.get("Gold", 0):
            raise ValueError(f"{table} 的养成货币必须为 Gold，不能归并到 Fons")
        counts[table] = rows["Gold"]

    stage_ids = tuple(row[0] for row in connection.execute(
        "SELECT DISTINCT drop_id FROM clone_activity_difficulty "
        "WHERE clone_id='Trailclone_gold' AND stamina_cost>0 ORDER BY drop_id"
    ))
    if not stage_ids:
        raise ValueError("Trailclone_gold 缺少正式付费副本档位")
    for drop_id in stage_ids:
        yields = connection.execute(
            "SELECT item_id,quantity FROM clone_drop_projection_item WHERE drop_id=?",
            (drop_id,),
        ).fetchall()
        if any(item_id == "Fons" for item_id, _quantity in yields) or not any(
            item_id == "Gold" and quantity > 0 for item_id, quantity in yields
        ):
            raise ValueError(f"Trailclone_gold 的 {drop_id} 缺少正式甲硬币产出")
    counts["paid_gold_stage_count"] = len(stage_ids)

    mammon = connection.execute(
        "SELECT item_id,quantity FROM clone_drop_projection_item WHERE drop_id='drop_fons1'"
    ).fetchall()
    if len(mammon) != 1 or mammon[0][0] != "Fons" or mammon[0][1] <= 0:
        raise ValueError("drop_fons1 必须保留正式方斯产出")
    return counts


__all__ = ["validate_reference_progression_currency"]
