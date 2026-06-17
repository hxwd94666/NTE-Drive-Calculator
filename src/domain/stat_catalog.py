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
        aliases = self.stat_alias_mapping or {}
        for candidate in candidates:
            resolved = aliases.get(candidate, candidate)
            if resolved in valid_stats:
                return resolved
            if candidate in valid_stats:
                return candidate

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

    def normalize_tape_main_stat(self, raw_name: Any, cutoff: float = 0.4) -> str:
        clean_name = str(raw_name or "").strip()
        if not clean_name:
            return "未知主词条"
        matches = difflib.get_close_matches(clean_name, self.tape_main_stats, n=1, cutoff=cutoff)
        return matches[0] if matches else "未知主词条"

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

    def flexible_weight_name(self, stat_name: str) -> str:
        return self.stat_alias_mapping.get(stat_name, stat_name)
