# 根据冻结方案判定套装件数并投影无条件常驻属性。
"""纯套装常驻投影；不访问 Qt、文件或数据库，也不把条件效果当常驻。"""

from __future__ import annotations

from collections.abc import Iterable, Mapping


def suit_permanent_source_unknown(items, suits, reviews) -> bool:
    """只有激活的效果需要来源证明；确认纯条件与缺资料严格区分。"""
    cores = [row for row in items if row.get('kind') == 'core' and not row.get('virtual')]
    if not cores:
        return False
    if len(cores) != 1:
        return True
    sid = str(cores[0].get('suit_id') or '')
    if sid not in suits:
        return True
    # 复用同一激活投影查询阈值，不复制件数口径（包括官方毕业形状证明）。
    probes = {sid: {str(n): [{'property_id': str(n), 'value': 1.0}] for n in (2,4)}}
    activated = project_suit_permanent_properties(items, suits, probes)
    return any(str(row['required_count']) not in ((reviews or {}).get(sid) or {}) for row in activated)


def project_suit_permanent_properties(
    items: Iterable[Mapping],
    suits: Mapping[str, Mapping],
    properties: Mapping,
) -> tuple[dict, ...]:
    """Return activated two/four-piece rows for one frozen loadout.

    ``suits`` supplies ``required_shape_ids`` and ``properties`` is keyed by
    ``(suit_id, required_count)``.  A core is required; two pieces need two
    distinct required shapes, while four pieces need all required shapes.
    Duplicate shapes and virtual placeholders never prove activation.
    Unknown source rows are omitted rather than represented as zero.
    """

    materialized = tuple(items)
    def shape(value):
        return str(value or "").strip().removeprefix("EquipmentGeometry_").casefold()

    cores = [item for item in materialized if item.get("kind") in {"core", "tape"}
             and not item.get("virtual")]
    if len(cores) != 1:
        return ()
    suit_id = str(cores[0].get("suit_id") or "")
    definition = suits.get(suit_id)
    if not definition:
        return ()
    required = {shape(value) for value in definition.get("required_shape_ids") or ()}
    if not required or "" in required:
        return ()
    # 驱动没有套装归属；核心决定套装，只有真实驱动的不同正式形状参与件数。
    shapes = {shape(item.get("shape_id") or item.get("geometry"))
              for item in materialized if item.get("kind") == "module" and not item.get("virtual")}
    # 构建期毕业模板聚合20格词条，但保留官方方案形状证明；它不是背包UID或普通虚拟占位。
    for item in materialized:
        if (item.get("kind") == "module" and item.get("item_id") == "graduation-module"
                and item.get("theoretical_source") == "official_graduation_template_v1" and not item.get("virtual")):
            shapes.update(shape(value) for value in item.get("graduation_assumed_shape_ids") or ())
    count = len(required & shapes)
    result: list[dict] = []
    for required_count in (2, 4):
        if count < required_count:
            continue
        rows = properties.get((suit_id, required_count), ())
        if not rows:
            rows = (properties.get(suit_id, {}) or {}).get(str(required_count), ())
        for row in rows:
            property_id = str(row.get("property_id") or "")
            if not property_id:
                continue
            result.append({
                "suit_id": suit_id,
                "required_count": required_count,
                "property_id": property_id,
                "value": float(row["value"]),
                "unit": str(row.get("unit") or "ratio"),
                "source_effect_id": row.get("source_effect_id"),
            })
    return tuple(result)
