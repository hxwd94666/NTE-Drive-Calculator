# 怪物与玩法域的正式玩法 Buff、选择与掉落投影。
"""Qt-free gameplay projections with complete player-facing rule details."""

from __future__ import annotations

from collections import Counter
import re
from typing import Any

from src.services.static_catalog_monster_display import (
    NAME_UNAVAILABLE,
    display_buff_option,
    display_catalog_scalar,
    display_damage_type,
)
from src.services.static_catalog_monster_models import (
    CatalogDetail,
    CatalogEntry,
    CatalogSection,
    CatalogValue,
)
from src.services.static_catalog_terminology_service import (
    StaticCatalogTerminologyService,
)


_STATUS_LABELS = {
    "complete": ("掉落已确认", "正式闭包已给出确定的掉落物与数量。"),
    "partial": ("部分信息可用", "仅展示正式闭包已经确认的掉落物与数量。"),
    "unavailable": ("掉落暂不可用", "当前没有可确认的固定掉落数量。"),
}
_GAP_LABELS = {
    "name_missing": "部分掉落物名称暂未提供；已确认数量仍按正式闭包展示。",
    "drop_group_missing": "正式掉落组暂未提供。",
    "sequence_branch_divergent": "掉落序列存在不同分支，无法确定唯一数量。",
    "sequence_not_deterministic": "掉落序列不是固定结果，未展示推测数量。",
}
_TRIGGER_LABELS = {
    "whole_battle": "持续生效",
    "corruption_damage_stack": "造成指定伤害后叠加",
    "while_target_toppled": "目标倾陷期间生效",
}


def _clean_description(value: object) -> str:
    text = re.sub(r"<[^>]+>", "", str(value or ""))
    return "\n".join(line.strip() for line in text.splitlines() if line.strip())


class StaticCatalogMonsterGameplayProjector:
    """Project formal gameplay rows without opening another DAO."""

    def __init__(self, terminology: StaticCatalogTerminologyService) -> None:
        self._terminology = terminology

    def witch_entries(self, rows: list[dict[str, Any]]) -> tuple[CatalogEntry, ...]:
        return tuple(
            CatalogEntry(
                key=f"witch_buff|{row['buff_id']}",
                domain="encounter",
                play_mode="witch_blessing",
                title=self._name(row.get("name_zh")),
                subtitle="战前可选的整场赐福",
                primary_id=str(row["buff_id"]),
                localization_available=self._available(row.get("name_zh")),
            )
            for row in rows
        )

    def witch_detail(self, row: dict[str, Any]) -> CatalogDetail:
        entry = self.witch_entries([row])[0]
        property_name = self._term_name("equipment_attribute", row.get("property_id"))
        amount = self._amount(row.get("property_value"), bool(row.get("is_percent")))
        section = CatalogSection(
            "魔女赐福",
            (
                CatalogValue(
                    label="赐福效果",
                    value=str(row.get("property_id") or ""),
                    provenance="official_static",
                    display_label=property_name,
                    display_value=amount,
                ),
            ),
            _clean_description(row.get("description_zh")),
        )
        return CatalogDetail(entry, (section,))

    def outer_buff_detail(self, row: dict[str, Any]) -> CatalogDetail:
        title = self._name(row.get("buff_name_zh"))
        entry = CatalogEntry(
            key=f"outer_buff|{row['level_config_id']}",
            domain="encounter",
            play_mode="outer_realm",
            title=title,
            subtitle=self._name(row.get("season_name_zh")),
            primary_id=str(row["level_config_id"]),
            secondary_id=str(row.get("buff_id") or ""),
            localization_available=self._available(row.get("buff_name_zh")),
        )
        values = tuple(
            CatalogValue(
                label="赛季 Buff 分量",
                value=str(component.get("property_id") or ""),
                provenance="official_static",
                display_label=self._term_name(
                    "equipment_attribute", component.get("property_id")
                ),
                display_value=self._component_description(component),
            )
            for component in row.get("components", ())
        )
        section = CatalogSection(
            "轨外赛季 Buff",
            values,
            _clean_description(row.get("description_zh")),
        )
        return CatalogDetail(entry, (section,))

    def feast_option(self, category: str, option: dict[str, Any]) -> CatalogValue:
        effect_kind = str(option.get("effect_kind") or "")
        display_category = (
            f"{display_damage_type(self._terminology, option.get('damage_type'))}提升"
            if effect_kind == "resistance_up"
            else category
        )
        return CatalogValue(
            label="争锋加成",
            value=str(option.get("option_id") or ""),
            provenance="official_static",
            display_label=display_category,
            display_value=display_buff_option(self._terminology, option),
            note=(
                "挑战时间规则不属于 Buff 乘区。"
                if effect_kind == "time_limit"
                else ""
            ),
        )

    def drop_section(self, projection: dict[str, Any] | None) -> CatalogSection:
        status = str((projection or {}).get("status") or "unavailable")
        status_label, status_copy = _STATUS_LABELS.get(
            status, _STATUS_LABELS["unavailable"]
        )
        values = [CatalogValue(
            label="掉落状态",
            value=status,
            provenance="official_static" if projection else "unavailable",
            display_label=status_label,
            display_value=status_copy,
        )]
        if projection is None:
            values.append(CatalogValue(
                label="信息缺口",
                value="",
                provenance="unavailable",
                display_label="待补全信息",
                display_value="该难度尚无正式掉落闭包，未展示推测数量。",
            ))
        for item in (projection or {}).get("items", ()):
            values.append(CatalogValue(
                label="掉落物",
                value=str(item.get("item_id") or ""),
                provenance="official_static",
                display_label=self._term_name("item", item.get("item_id")),
                display_value=f"× {int(item['quantity'])}",
            ))
        counts = Counter(
            str(gap.get("reason_code") or "")
            for gap in (projection or {}).get("gaps", ())
        )
        for reason_code, count in counts.items():
            message = _GAP_LABELS.get(
                reason_code,
                "正式掉落闭包仍有未解析信息，未展示推测数量。",
            )
            values.append(CatalogValue(
                label="信息缺口",
                value=reason_code,
                provenance="unavailable",
                display_label="待补全信息",
                display_value=(f"{count} 项：{message}" if count > 1 else message),
            ))
        return CatalogSection("正式掉落", tuple(values), status_copy)

    def _term_name(self, entity_kind: str, stable_id: object) -> str:
        identity = str(stable_id or "").strip()
        if not identity:
            return NAME_UNAVAILABLE
        term = self._terminology.resolve(entity_kind, identity)
        return term.display_name if term.name_available else NAME_UNAVAILABLE

    @staticmethod
    def _amount(value: object, is_percent: bool) -> str:
        number = float(value)
        return f"提升 {number * 100:g}%" if is_percent else f"增加 {number:g}"

    @staticmethod
    def _component_description(component: dict[str, Any]) -> str:
        parts = [
            _TRIGGER_LABELS.get(
                str(component.get("trigger_kind") or ""),
                "生效条件见正式说明",
            ),
            f"数值 +{display_catalog_scalar(component.get('property_value'))}",
        ]
        if component.get("duration_seconds") is not None:
            parts.append(f"持续 {display_catalog_scalar(component['duration_seconds'])} 秒")
        if int(component.get("stack_limit_count") or 1) > 1:
            parts.append(f"最多 {int(component['stack_limit_count'])} 层")
        if component.get("trigger_cooldown_seconds") is not None:
            parts.append(
                f"触发间隔 {display_catalog_scalar(component['trigger_cooldown_seconds'])} 秒"
            )
        return " · ".join(parts)

    @staticmethod
    def _available(value: object) -> bool:
        text = str(value or "").strip()
        return bool(text and "\ufffd" not in text)

    @classmethod
    def _name(cls, value: object) -> str:
        return str(value).strip() if cls._available(value) else NAME_UNAVAILABLE
