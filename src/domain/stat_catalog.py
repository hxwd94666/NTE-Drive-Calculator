# 统一管理词条合法池、主词条池和 OCR 别名归一化。
"""Canonical stat catalog used by parser, scoring, UI, and extension code."""

from __future__ import annotations

import difflib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.storage.json_store import read_json
from src.utils.name_resolver import resolve_name


@dataclass(frozen=True)
class StatCatalog:
    ATTRIBUTE_SUB_STAT_ALIASES = {
        "光": "光属性异能伤害增强%",
        "灵": "灵属性异能伤害增强%",
        "咒": "咒属性异能伤害增强%",
        "暗": "暗属性异能伤害增强%",
        "魂": "魂属性异能伤害增强%",
        "相": "相属性异能伤害增强%",
        "心灵": "心灵伤害增强%",
    }
    WEIGHT_NAME_ALIASES = {
        "通用伤害增强": "伤害增加%",
        "通用伤害增强%": "伤害增加%",
    }
    OCR_SHORT_STAT_ALIASES = {
        "攻击": "攻击力",
        "防御": "防御力",
        "生命": "生命值",
        "小攻击": "攻击力",
        "小防御": "防御力",
        "小生命": "生命值",
    }

    gold_base_values: dict[str, float] = field(default_factory=dict)
    tape_main_stats: list[str] = field(default_factory=list)
    tape_main_values: dict[str, float] = field(default_factory=dict)
    tape_stat_values: dict[str, float] = field(default_factory=dict)
    main_only_keywords: list[str] = field(default_factory=list)
    stat_alias_mapping: dict[str, str] = field(default_factory=dict)
    benefit_one: dict[str, float] = field(default_factory=dict)
    benefit_alias_mapping: dict[str, str] = field(default_factory=dict)
    weight_pool: list[str] = field(default_factory=list)

    @classmethod
    def from_config_dir(cls, config_dir: str | Path = "config") -> "StatCatalog":
        data = read_json(Path(config_dir) / "stats.json", default={}) or {}
        return cls(
            gold_base_values=data.get("gold_base_values", {}) or {},
            tape_main_stats=data.get("tape_main_stats_pool", []) or [],
            tape_main_values=data.get("tape_main_stat_values", {}) or {},
            tape_stat_values=data.get("tape_stat_values", {}) or {},
            main_only_keywords=data.get("main_only_keywords", []) or [],
            stat_alias_mapping=data.get("stat_alias_mapping", {}) or {},
            benefit_one=data.get("benefit_one", {}) or {},
            benefit_alias_mapping=data.get("benefit_alias_mapping", {}) or {},
            weight_pool=data.get("weight_pool", []) or [],
        )

    @property
    def valid_sub_stats(self) -> set[str]:
        return set(self.gold_base_values.keys())

    def _weight_aliases(self) -> dict[str, str]:
        aliases = dict(self.WEIGHT_NAME_ALIASES)
        aliases.update(self.stat_alias_mapping or {})
        return aliases

    def normalize_stat_name(self, raw_name: Any, is_percent: bool = False, cutoff: float = 0.72) -> str | None:
        name = str(raw_name or "").strip()
        if not name:
            return None

        candidates = []
        if is_percent:
            candidates.append(f"{name}%")
        candidates.append(name)
        expanded_candidates = []
        for candidate in candidates:
            expanded_candidates.append(candidate)
            for suffix in ("增加", "提升", "增强"):
                expanded_candidates.append(candidate.replace(suffix, ""))
        candidates = [candidate for candidate in dict.fromkeys(expanded_candidates) if candidate]

        valid_stats = self.valid_sub_stats
        aliases = self._weight_aliases()
        for candidate in candidates:
            short_name = self.OCR_SHORT_STAT_ALIASES.get(candidate.rstrip("%"))
            if short_name:
                resolved_short_name = f"{short_name}%" if candidate.endswith("%") else short_name
                if resolved_short_name in valid_stats:
                    return resolved_short_name
            resolved = aliases.get(candidate, candidate)
            if resolved in valid_stats:
                return resolved
            if candidate in valid_stats:
                return candidate

        protected = self._match_attribute_sub_stat(candidates)
        if protected:
            return protected
        if any(self._looks_like_attribute_sub_stat(candidate) for candidate in candidates):
            return None

        pool = sorted(valid_stats | set(aliases.keys()) | set(aliases.values()))
        for candidate in candidates:
            match = resolve_name(candidate, pool, cutoff=cutoff)
            if not match:
                matches = difflib.get_close_matches(candidate, pool, n=1, cutoff=cutoff)
                match = matches[0] if matches else None
            if not match:
                continue
            resolved = aliases.get(match, match)
            if resolved in valid_stats:
                return resolved
        return None

    def _match_attribute_sub_stat(self, candidates: list[str]) -> str | None:
        matched = []
        valid_stats = self.valid_sub_stats
        for candidate in candidates:
            if "心灵" in candidate and self.ATTRIBUTE_SUB_STAT_ALIASES["心灵"] in valid_stats:
                matched.append(self.ATTRIBUTE_SUB_STAT_ALIASES["心灵"])
                continue
            for key, stat_name in self.ATTRIBUTE_SUB_STAT_ALIASES.items():
                if key == "心灵":
                    continue
                if key in candidate and stat_name in valid_stats:
                    matched.append(stat_name)
        unique = list(dict.fromkeys(matched))
        if len(unique) == 1:
            return unique[0]
        return None

    def _looks_like_attribute_sub_stat(self, candidate: str) -> bool:
        return (
            "属性" in candidate
            or "异能伤害" in candidate
            or "伤害增强" in candidate
            or "心灵伤害" in candidate
        )

    def normalize_tape_main_stat(self, raw_name: Any, cutoff: float = 0.4) -> str:
        clean_name = str(raw_name or "").strip()
        if not clean_name:
            return "未知主词条"
        aliases = self._weight_aliases()
        short_name = self.OCR_SHORT_STAT_ALIASES.get(clean_name.rstrip("%"))
        if short_name:
            # 卡带的三类基础面板主词条均为百分比，OCR 常遗漏“百分比”二字。
            candidate = f"{short_name}%"
            if candidate in self.tape_main_values:
                return candidate
        resolved = aliases.get(clean_name, clean_name)
        if resolved in self.tape_main_values:
            return resolved
        matches = difflib.get_close_matches(clean_name, self.tape_main_stats, n=1, cutoff=cutoff)
        if not matches:
            return "未知主词条"
        matched = aliases.get(matches[0], matches[0]).replace("百分比", "%")
        return matched if matched in self.tape_main_values else "未知主词条"

    def weight_choice_pool(self) -> list[str]:
        """Return canonical stat names that can be used in role weight config."""

        if self.weight_pool:
            return list(dict.fromkeys(str(stat).strip() for stat in self.weight_pool if str(stat).strip()))

        pool = set(self.gold_base_values.keys())
        pool.update(self.tape_main_values.keys())

        valid_targets = pool | set(self.tape_main_values.keys())
        for raw_name, target_name in (self.stat_alias_mapping or {}).items():
            raw = str(raw_name or "").strip()
            target = str(target_name or "").strip()
            if target in valid_targets:
                pool.add(target)
            elif raw in valid_targets:
                pool.add(raw)

        return sorted(stat for stat in pool if stat)

    def tape_main_stat_pool(self) -> list[str]:
        """Strict card-tape main-stat pool, excluding aliases and sub stats."""
        return list(dict.fromkeys(str(stat).strip() for stat in self.tape_main_values if str(stat).strip()))

    def tape_sub_stat_pool(self) -> list[str]:
        """Strict card-tape sub-stat pool, never including main-only stats."""
        main_only = tuple(str(keyword).strip() for keyword in self.main_only_keywords if str(keyword).strip())
        return list(dict.fromkeys(
            stat
            for stat in (str(raw_stat).strip() for raw_stat in self.tape_stat_values)
            if stat and not any(keyword in stat for keyword in main_only)
        ))

    def flexible_weight_name(self, stat_name: str) -> str:
        return self._weight_aliases().get(stat_name, stat_name)
