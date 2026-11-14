# 将一次配装属性汇总的所有着色权重合并查询，避免逐行启动独立分析进程。
from __future__ import annotations

from collections.abc import Mapping

from src.features.inventory.equipment.display.plan_projection import display_stat_label


def summary_weight_names(row, *, text_catalog=None):
    label = str(row.label)
    names = tuple(display_stat_label(identity) for identity in (getattr(row, "weight_property_ids", ()) or ()))
    fallback = display_stat_label(str(row.key), fallback=label) if str(row.key).endswith("Up") else label
    return tuple(dict.fromkeys((*(names or (fallback,)), label)))


def prepare_attribute_weights(presentation, role_name, state, *, text_catalog=None):
    names = []
    states = [state, state.get("_game_saved_state")]
    for candidate in states:
        if not isinstance(candidate, Mapping):
            continue
        for key in ("_official_attribute_summaries", "_official_previous_attribute_summaries"):
            summaries = candidate.get(key)
            if isinstance(summaries, Mapping):
                for mode in ("equipment", "character"):
                    for row in summaries.get(mode, ()):
                        names.extend(summary_weight_names(row, text_catalog=text_catalog))
    if not names:
        return lambda stat, mode: presentation.attribute_summary_weight(role_name, stat, mode)
    prepare = getattr(presentation, "attribute_summary_weights", None)
    if not callable(prepare):
        return lambda stat, mode: presentation.attribute_summary_weight(role_name, stat, mode)
    # 数值仍由公共 Rust 核计算，本层只读批量结果，不实现第二份别名或评分公式。
    resolved = prepare(role_name, tuple(dict.fromkeys(names)))
    return lambda stat, mode: resolved[(stat, mode)]
