# 批量消费共享 Rust 评分核；有界缓存只属于本评分器，缓存键保留权重顺序及全部评分事实。
from __future__ import annotations

from collections import OrderedDict
from collections.abc import Mapping
from dataclasses import asdict
import json

from src.integrations.analysis_core_release import create_bundled_analysis_client
from src.integrations.bundled_resources import bundled_root
from src.integrations.equipment_scoring import freeze_equipment_scoring
from src.integrations.nte_analysis_core import NativeAnalysisError


def _key(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def score_item(item, kind=None):
    """只传评分真正消费的原值；词条数值/UID不改变基础评分，卡带实际主词条值会改变。"""
    raw = item if isinstance(item, Mapping) else vars(item)
    kind = kind or raw["item_type"]
    return {"item_type": kind, "area": raw.get("area", 15 if kind == "tape" else 0),
            "quality": raw.get("quality", "Gold"), "sub_stats": dict(raw.get("sub_stats") or {}),
            "main_stats": raw.get("main_stats") if kind == "tape" else None,
            "main_value": raw.get("main_value") if kind == "tape" else None}


class NativeEquipmentScorer:
    def __init__(self, catalog, quality_map, static_database_path, client=None):
        self.catalog = catalog
        self.quality_map = quality_map
        self.static_database_path = static_database_path
        self.client = client
        self._facts = None
        self._scores = OrderedDict()
        self._maximums = OrderedDict()
        self._weights = OrderedDict()
        self._grades = OrderedDict()

    @staticmethod
    def _put(cache, key, value):
        cache[key] = value
        cache.move_to_end(key)
        if len(cache) > 8192:
            cache.popitem(last=False)

    def _sync(self):
        facts = _key([asdict(self.catalog), self.quality_map])
        if facts != self._facts:
            self._facts = facts
            for cache in (self._scores, self._maximums, self._weights, self._grades):
                cache.clear()

    def _client(self):
        if self.client is None:
            self.client = create_bundled_analysis_client(
                static_database_path=self.static_database_path or bundled_root() / "data/game_static.sqlite3")
        if self.client is None or not self.client.supports_equipment_scoring:
            raise NativeAnalysisError("分析组件缺少公共 Rust 评分能力，请更新配套组件")
        return self.client

    @staticmethod
    def _profile(weights, main_weights=None, maximum=None, zeros=()):
        return {"key": "profile", "weights": dict(weights), "main_weights": main_weights,
                "max_weight": maximum, "zero_weight_stats": list(zeros or ())}

    @staticmethod
    def _score_key(item, profile, maximum):
        # 副词条顺序影响浮点加法次序，和权重别名顺序一样保留，不按集合排序。
        return _key([item["item_type"], item["area"], item["quality"], list(item["sub_stats"]),
                     item["main_stats"], item["main_value"], profile["weights"], maximum,
                     profile.get("main_weights") if item["item_type"] == "tape" else None,
                     list(profile.get("zero_weight_stats") or ()) if item["item_type"] == "drive" else []])

    def batch(self, items, profiles, *, queries=(), grade_queries=(), checkpoint=None):
        self._sync()
        items, profiles = [score_item(item) for item in items], list(profiles)
        if len(profiles) > 128 or len(items) > 20_000 or len(items) * len(profiles) > 1_000_000:
            raise NativeAnalysisError("本批评分矩阵超过单次上限，请分批提交")
        frozen = freeze_equipment_scoring(self.catalog, items, profiles, queries=queries,
            grade_queries=grade_queries, quality_map=self.quality_map, checkpoint=checkpoint)
        result = self._client().score_equipment(frozen, checkpoint=checkpoint)
        for profile, row in zip(profiles, result["profiles"]):
            weights = _key(profile["weights"])
            maximum_key = _key([profile["weights"], list(profile.get("zero_weight_stats") or ())])
            if profile.get("max_weight") is None:
                self._put(self._maximums, maximum_key, row["max_weight"])
            for query, value in zip(queries, row["weights"]):
                self._put(self._weights, (weights, query), value)
            for index, (item, score, grade) in enumerate(zip(items, row["scores"], row["grades"])):
                if checkpoint is not None and index % 64 == 0:
                    checkpoint()
                self._put(self._scores, self._score_key(score_item(item), profile, row["max_weight"]), score)
                self._put(self._grades, (score, score_item(item)["area"]), grade)
        for query, grade in zip(grade_queries, result["grade_results"]):
            self._put(self._grades, (query["score"], query["area"]), grade)
        return result

    def prepare(self, items, profiles, *, checkpoint=None, consume=None):
        """热点调用先成批预热，保留方法级兼容入口但不在 N×R 循环中逐件启动进程。"""
        self._sync()
        items, profiles = list(items), list(profiles)
        if not items or not profiles:
            return
        # 单次请求受协议矩阵上限约束；取消贯穿每一块，不静默截断其余装备。
        width = min(128, max(1, 1_000_000 // min(len(items), 20_000)))
        for start in range(0, len(profiles), width):
            selected = profiles[start:start + width]
            for offset in range(0, len(items), 20_000):
                chunk = items[offset:offset + 20_000]
                if checkpoint is not None:
                    checkpoint()
                if consume is None:
                    ready = True
                    for profile in selected:
                        maximum = profile.get("max_weight")
                        if maximum is None:
                            maximum = self._maximums.get(_key([profile["weights"], list(profile.get("zero_weight_stats") or ())]))
                        if maximum is None or any(self._score_key(score_item(item), profile, maximum) not in self._scores for item in chunk):
                            ready = False
                            break
                    if ready:
                        continue
                queries = list(dict.fromkeys([*self.catalog.gold_base_values, *self.catalog.tape_main_values,
                    *self.catalog.stat_alias_mapping, *self.catalog.stat_alias_mapping.values(),
                    *(name for profile in selected for name in profile["weights"])]))
                result = self.batch(chunk, selected, queries=queries, checkpoint=checkpoint)
                if consume is not None:
                    consume(chunk, result)

    def maximum(self, weights, zeros=()):
        self._sync()
        key = _key([weights, list(zeros or ())])
        if key not in self._maximums:
            queries = list(dict.fromkeys([*weights, *self.catalog.gold_base_values,
                *self.catalog.tape_main_values, *self.catalog.stat_alias_mapping, *self.catalog.stat_alias_mapping.values()]))
            self.batch([], [self._profile(weights, zeros=zeros)], queries=queries)
        return self._maximums[key]

    def weight(self, name, weights):
        self._sync()
        key = (_key(weights), str(name or ""))
        if key not in self._weights:
            self.batch([], [self._profile(weights)], queries=[key[1]])
        return self._weights[key]

    def score(self, item, weights, maximum, *, kind, main_weights=None, zeros=()):
        self._sync()
        item = score_item(item, kind)
        profile = self._profile(weights, main_weights, maximum, zeros)
        key = self._score_key(item, profile, maximum)
        if key not in self._scores:
            self.batch([item], [profile])
        return self._scores[key]

    def grade(self, score, area):
        self._sync()
        key = (score, area)
        if key not in self._grades:
            self.batch([], [], grade_queries=[{"score": score, "area": area}])
        return self._grades[key]

    def accept_grades(self, rows):
        """只由已核对组件/输入身份的正式作业调用，不重新数值求值或启动第二进程。"""
        self._sync()
        for score, area, grade in rows:
            if grade not in {"ACE", "SSS", "SS", "S", "A", "B", "C", "D"}:
                raise NativeAnalysisError("原生作业评级证明无效")
            self._put(self._grades, (score, area), grade)
