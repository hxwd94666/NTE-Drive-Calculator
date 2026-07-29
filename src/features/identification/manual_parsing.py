# 解析手工鉴定文本并规范化词条名称与展示数值。
"""Manual-input parsing behavior shared by the identification controller."""

from __future__ import annotations

import re

from src.features.identification.lifecycle import (
    task_identification_dependencies,
)
from src.optimizer.scoring import ScoringEngine
from src.utils.name_resolver import resolve_name


class IdentificationManualParsingMixin:
    """Keep manual text parsing separate from capture and worker lifecycle."""

    def _apply_identify_manual_fields(self, text):
        if not text:
            return
        lower = text.lower()
        if "卡带" in text or "tape" in lower:
            self.ident_tape_rb.setChecked(True)
        elif "驱动" in text or "drive" in lower:
            self.ident_drive_rb.setChecked(True)

        if "purple" in lower or "紫" in text:
            self._set_combo_data(self.ident_quality_combo, "Purple")
        elif "blue" in lower or "蓝" in text:
            self._set_combo_data(self.ident_quality_combo, "Blue")
        elif "gold" in lower or "金" in text:
            self._set_combo_data(self.ident_quality_combo, "Gold")

        for shape_id in self._shape_areas:
            if shape_id != "TAPE_15" and shape_id in text:
                self._set_combo_data(self.ident_shape_combo, shape_id)
                break

        for token in self._manual_tokens(text):
            if "套装" in token or "set" in token.lower():
                value = self._manual_value(token)
                resolved = resolve_name(
                    value,
                    self.all_set_names,
                    cutoff=0.55,
                )
                if resolved:
                    self._set_combo_data(self.ident_set_combo, resolved)
            if (
                "主词条" in token
                or "主属性" in token
                or "main" in token.lower()
            ):
                value = self._manual_value(token)
                resolved = resolve_name(
                    value,
                    self._get_tape_main_stats_pool(),
                    cutoff=0.55,
                )
                if resolved:
                    self._set_combo_data(self.ident_main_combo, resolved)
        self._on_identify_type_changed()

    def _manual_tokens(self, text):
        del self
        normalized = re.sub(
            r"[-+]?\d+(?:\.\d+)?\s*%",
            "%",
            str(text or ""),
        )
        normalized = re.sub(r"[-+]?\d+(?:\.\d+)?", "", normalized)
        normalized = re.sub(r"\s+%", "%", normalized)
        return [
            part.strip()
            for part in re.split(r"[\s,，;；]+", normalized)
            if part.strip()
        ]

    def _manual_value(self, token):
        del self
        for separator in ("：", ":", "="):
            if separator in token:
                return token.split(separator, 1)[1].strip()
        return token.strip()

    def _resolve_stat_name(self, name, percent=False):
        clean = name.strip().strip("：:= ")
        for prefix in ("副词条", "词条", "主词条", "主属性"):
            clean = clean.replace(prefix, "")
        clean = clean.strip()
        manual_aliases = {
            "大生命": "生命值%",
            "生命%": "生命值%",
            "生命值%": "生命值%",
            "生命值百分比": "生命值%",
            "百分比生命值": "生命值%",
            "大防御": "防御力%",
            "防御": "防御力",
            "防御%": "防御力%",
            "防御力%": "防御力%",
            "防御力百分比": "防御力%",
            "百分比防御力": "防御力%",
            "大攻击": "攻击力%",
            "攻击": "攻击力",
            "攻击%": "攻击力%",
            "攻击力%": "攻击力%",
            "攻击力百分比": "攻击力%",
            "百分比攻击力": "攻击力%",
            "暴击": "暴击率%",
            "爆击": "暴击率%",
            "暴击率": "暴击率%",
            "爆击率": "暴击率%",
            "爆伤": "暴击伤害%",
            "暴伤": "暴击伤害%",
            "暴击伤害": "暴击伤害%",
            "伤害增加": "伤害增加%",
            "通用伤害": "伤害增加%",
            "通伤": "伤害增加%",
            "伤害": "伤害增加%",
            "倾陷": "倾陷强度",
            "倾陷强度": "倾陷强度",
            "环合": "环合强度",
            "环合强度": "环合强度",
        }
        compact = clean.replace(" ", "")
        if compact in manual_aliases:
            return manual_aliases[compact]
        if percent and not clean.endswith("%") and not clean.endswith("百分比"):
            clean = f"{clean}%"
        scoring_engine = getattr(self, "scoring_engine", None)
        if scoring_engine is None:
            dependencies = task_identification_dependencies(self)
            scoring_engine = ScoringEngine(str(dependencies.config_dir))
        aliases = scoring_engine.stat_alias_mapping
        if clean in aliases:
            return aliases[clean]
        choices = [
            *scoring_engine.gold_base_values,
            *aliases,
            *aliases.values(),
        ]
        resolved = resolve_name(clean, choices, cutoff=0.62)
        if resolved in aliases:
            return aliases[resolved]
        return resolved or clean

    @staticmethod
    def _manual_drive_main_stats(area, quality):
        quality_coefficient = {
            "Gold": 1.0,
            "Purple": 0.8,
            "Blue": 0.6,
        }.get(str(quality or "Gold"), 1.0)
        return {
            "攻击力": round(21.0 * int(area) * quality_coefficient, 2),
            "生命值": round(280.0 * int(area) * quality_coefficient, 2),
        }

    def _parse_manual_stats(
        self,
        text,
        quality="Gold",
        grid_equivalent=1,
    ):
        stats = {}
        scoring_engine = getattr(self, "scoring_engine", None)
        if scoring_engine is None:
            dependencies = task_identification_dependencies(self)
            scoring_engine = ScoringEngine(str(dependencies.config_dir))
        quality_coefficient = {
            "Gold": 1.0,
            "Purple": 0.8,
            "Blue": 0.6,
        }.get(str(quality or "Gold"), 1.0)
        grid_count = max(1, int(grid_equivalent or 1))
        for token in self._manual_tokens(text):
            if any(
                keyword in token
                for keyword in (
                    "类型",
                    "品质",
                    "形状",
                    "套装",
                    "主词条",
                    "主属性",
                )
            ):
                continue
            stat_name = self._resolve_stat_name(
                token,
                percent="%" in token,
            )
            if stat_name not in scoring_engine.gold_base_values:
                continue
            stats[stat_name] = round(
                float(scoring_engine.gold_base_values[stat_name])
                * grid_count
                * quality_coefficient,
                2,
            )
        return stats
