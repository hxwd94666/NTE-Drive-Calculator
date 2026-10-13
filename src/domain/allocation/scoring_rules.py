# 深冻结词条评分九字段；每次物化独享目录，纯值对象不读取路径或角色数据库。
from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import json
from math import isfinite
from collections.abc import Mapping

from src.domain.stat_catalog import StatCatalog


_NUMERIC = ("gold_base_values", "tape_main_stat_values", "tape_stat_values", "benefit_one")
_ALIASES = ("stat_alias_mapping", "benefit_alias_mapping")
_LISTS = ("tape_main_stats_pool", "main_only_keywords", "weight_pool")


@dataclass(frozen=True, slots=True)
class AllocationScoringRules:
    gold_base_values: tuple[tuple[str, float], ...]
    tape_main_stats_pool: tuple[str, ...]
    tape_main_stat_values: tuple[tuple[str, float], ...]
    tape_stat_values: tuple[tuple[str, float], ...] = ()
    main_only_keywords: tuple[str, ...] = ()
    stat_alias_mapping: tuple[tuple[str, str], ...] = ()
    benefit_one: tuple[tuple[str, float], ...] = ()
    benefit_alias_mapping: tuple[tuple[str, str], ...] = ()
    weight_pool: tuple[str, ...] = ()
    source_sha256: str = ""

    def __post_init__(self):
        # frozen仅阻止属性赋值；这里还断开所有调用方可变容器的引用。
        for name in (*_NUMERIC, *_ALIASES):
            source = getattr(self, name)
            pairs = source.items() if isinstance(source, Mapping) else source
            copied = []
            seen = set()
            for key, value in pairs:
                if not isinstance(key, str) or not key or key in seen:
                    raise ValueError(f"评分规则{name}的键无效或重复")
                seen.add(key)
                if name in _NUMERIC:
                    if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value):
                        raise ValueError(f"评分规则{name}.{key}的数值无效")
                    value = float(value)
                elif not isinstance(value, str):
                    raise ValueError(f"评分规则{name}.{key}的别名无效")
                copied.append((key, value))
            object.__setattr__(self, name, tuple(copied))
        for name in _LISTS:
            source = getattr(self, name)
            if isinstance(source, (str, bytes)):
                raise ValueError(f"评分规则{name}必须是文本列表")
            source = tuple(source)
            if any(not isinstance(value, str) for value in source):
                raise ValueError(f"评分规则{name}必须是文本列表")
            object.__setattr__(self, name, tuple(source))
        if not isinstance(self.source_sha256, str):
            raise ValueError("评分规则来源哈希必须是文本")
        if self.source_sha256 and (len(self.source_sha256) != 64 or any(c not in "0123456789abcdef" for c in self.source_sha256)):
            raise ValueError("评分规则来源哈希无效")

    @classmethod
    def from_data(cls, data: Mapping, *, source_sha256: str = ""):
        if not isinstance(data, Mapping):
            raise ValueError("评分规则必须是对象")
        # 允许既有目录中的可选空字段，不用空规则冒充缺失的必要证据。
        mandatory = ("gold_base_values", "tape_main_stats_pool", "tape_main_stat_values")
        if any(name not in data or data[name] is None for name in mandatory):
            raise ValueError("评分规则缺少基础值或卡带主词条目录")
        for name in (*_NUMERIC, *_ALIASES):
            if name in data and data[name] is not None and not isinstance(data[name], Mapping):
                raise ValueError(f"评分规则{name}必须是对象")
        return cls(**{name: data.get(name) or () for name in (*_NUMERIC, *_ALIASES, *_LISTS)},
                   source_sha256=source_sha256)

    def to_stat_catalog(self) -> StatCatalog:
        # 每个solver/Top-K分支独享可变目录，不跨请求共享引擎或装备投影。
        payload = {name: dict(getattr(self, name)) for name in (*_NUMERIC, *_ALIASES)}
        payload.update({name: list(getattr(self, name)) for name in _LISTS})
        return StatCatalog.from_data(payload)

    @property
    def rule_sha256(self) -> str:
        # 与FrozenComparisonScorer既有StatCatalog规范化元数据完全同口径。
        raw = json.dumps(asdict(self.to_stat_catalog()), sort_keys=True, ensure_ascii=False)
        return sha256(raw.encode("utf-8")).hexdigest()
