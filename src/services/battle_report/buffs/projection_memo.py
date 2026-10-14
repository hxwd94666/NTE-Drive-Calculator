# 在一次反事实请求中驻留精确命中签名并复用独立区间规则结果。
from __future__ import annotations

from collections import OrderedDict
from dataclasses import replace

from src.domain.battle_report.timeline.events import BattleAnalysisHit
from src.domain.battle_report.buffs.models import BattleHitBuffProjection, BattleInferredBuffInterval
from src.services.battle_report.buffs.projection_rules import IntervalProjectionEvaluation, evaluate_interval_projection
from src.domain.battle_report.analysis.backend_contracts import BuffProjectionBackend, DirectFormulaBackend
from src.services.battle_report.native.buff_projection import NativeBuffProjectionBatch
from src.services.battle_report.analysis.progress import BattleAnalysisProgressCallback, report_battle_analysis_progress
from src.services.battle_report.buffs.interval_index import BattleBuffIntervalQuery


def hit_projection_signature(hit: BattleAnalysisHit) -> BattleAnalysisHit:
    """Retain every field except the same observational fields excluded by the old exact cache."""
    return replace(
        hit,
        event_id="",
        sequence=0,
        relative_time_us=0,
        damage=0.0,
        raw_damage=None,
        overkill_damage=None,
        damage_correction_kind="",
        damage_correction_confidence="",
        damage_correction_basis="",
        damage_overlap_correction=0.0,
    )


class BattleBuffProjectionMemo:
    """Request-owned caches; all identities retain strong references and never escape the request."""

    def __init__(self, backend: DirectFormulaBackend | None = None,
                 progress_callback: BattleAnalysisProgressCallback | None = None) -> None:
        self.native: NativeBuffProjectionBatch | None = None
        self.bind_backend(backend, progress_callback)
        self._hit_objects: OrderedDict[int, tuple[BattleAnalysisHit, int]] = OrderedDict()
        self._signatures: dict[BattleAnalysisHit, int] = {}
        self._interval_objects: dict[int, tuple[BattleInferredBuffInterval, int]] = {}
        self._interval_values: dict[BattleInferredBuffInterval, int] = {}
        self._interval_queries: dict[int, tuple[BattleBuffIntervalQuery, int, tuple[int, ...]]] = {}
        self._interval_sets: dict[tuple[int, ...], int] = {}
        self._evaluations: dict[tuple[int, int, str], IntervalProjectionEvaluation] = {}
        self._projections: OrderedDict[object, BattleHitBuffProjection] = OrderedDict()
        self.rule_evaluations = 0
        self.rule_reuses = 0
        self.projection_reuses = 0

    def bind_backend(self, backend: DirectFormulaBackend | None,
                     progress_callback: BattleAnalysisProgressCallback | None = None) -> None:
        if not isinstance(backend, BuffProjectionBackend):
            return
        if self.native is None:
            self.native = NativeBuffProjectionBatch(backend)
        elif self.native.backend is not backend:
            raise ValueError("Projection memo belongs to another frozen analysis backend")
        self.native.checkpoint = lambda: report_battle_analysis_progress(
            progress_callback, phase="buff_projection", message="正在批量计算逐击 Buff 规则与投影…",
        )

    def hit_token(self, hit: BattleAnalysisHit) -> int:
        identity = id(hit)
        existing = self._hit_objects.get(identity)
        if existing is not None:
            self._hit_objects.move_to_end(identity)
            return existing[1]
        signature = hit_projection_signature(hit)
        token = self._signatures.get(signature)
        if token is None:
            token = len(self._signatures)
            self._signatures[signature] = token
        self._hit_objects[identity] = (hit, token)
        if len(self._hit_objects) > 4096:
            self._hit_objects.popitem(last=False)
        return token

    def interval_token(self, interval: BattleInferredBuffInterval) -> int:
        identity = id(interval)
        existing = self._interval_objects.get(identity)
        if existing is None:
            # 只有全部不可变字段相等才共享 token；保留原对象，避免身份回收误命中。
            token = self._interval_values.get(interval)
            if token is None:
                token = len(self._interval_values)
                self._interval_values[interval] = token
            existing = (interval, token)
            self._interval_objects[identity] = existing
        return existing[1]

    def evaluate_interval(
        self, hit_token: int, hit: BattleAnalysisHit, interval: BattleInferredBuffInterval, channel_id: str
    ) -> IntervalProjectionEvaluation:
        key = (hit_token, self.interval_token(interval), channel_id)
        result = self._evaluations.get(key)
        if result is None:
            result = evaluate_interval_projection(hit, interval, channel_id)
            self._evaluations[key] = result
            self.rule_evaluations += 1
        else:
            self.rule_reuses += 1
        return result

    def interval_set(self, query: BattleBuffIntervalQuery) -> tuple[int, tuple[int, ...]]:
        """Intern a complete ordered candidate once, before its per-hit loop."""
        identity = id(query)
        existing = self._interval_queries.get(identity)
        if existing is None:
            tokens = tuple(self.interval_token(interval) for interval in query)
            token = self._interval_sets.get(tokens)
            if token is None:
                token = len(self._interval_sets)
                self._interval_sets[tokens] = token
            existing = (query, token, tokens)
            self._interval_queries[identity] = existing
        return existing[1], existing[2]

    def projection(self, key: object) -> BattleHitBuffProjection | None:
        result = self._projections.get(key)
        if result is not None:
            self._projections.move_to_end(key)
            self.projection_reuses += 1
        return result

    def remember_projection(self, key: object, result: BattleHitBuffProjection) -> None:
        self._projections[key] = result
        self._projections.move_to_end(key)
        # 每次请求持有完整证据；多候选之间复用但限制驻留结果数量。
        if len(self._projections) > 4096:
            self._projections.popitem(last=False)
