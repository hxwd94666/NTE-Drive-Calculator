# 复用命中语义与区间集合完全一致的 Buff 投影结果。

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Iterable
from dataclasses import replace

from src.domain.battle_report import (
    BattleAnalysisHit,
    BattleHitBuffProjection,
)
from src.services.battle_report.buffs.projection_memo import BattleBuffProjectionMemo
from src.services.battle_report.buffs.attribute_projection import (
    BattleBuffAttributeProjectionService,
)
from src.services.battle_report.buffs.interval_index import (
    BattleBuffIntervalQuery,
    buff_interval_applies_to_hit,
)


class BattleHitBuffProjectionCache:
    """Cache only exact immutable hit/active/temporal projection inputs."""

    def __init__(self, intervals: BattleBuffIntervalQuery, *, memo: BattleBuffProjectionMemo | None = None) -> None:
        self._intervals = intervals
        self.memo = memo if memo is not None else BattleBuffProjectionMemo()
        self._by_hit_identity: OrderedDict[int, tuple[BattleAnalysisHit, BattleHitBuffProjection]] = OrderedDict()
        self._prepared: dict[int, tuple[BattleAnalysisHit, BattleHitBuffProjection]] = {}
        # The owner fixes the complete interval query; time still identifies Fork
        # boundaries that the full semantic hit signature intentionally excludes.
        self._prepared_keys: dict[tuple[int, int], BattleHitBuffProjection] = {}

    def require_intervals(self, intervals: BattleBuffIntervalQuery) -> None:
        if self._intervals is not intervals:
            raise ValueError("Projection cache belongs to another frozen interval query")

    def _input(self, hit: BattleAnalysisHit, *, hit_token: int):
        temporal = self._intervals.temporal_for_hit(hit)
        active = tuple(interval for interval in temporal if buff_interval_applies_to_hit(interval, hit))
        key = (hit_token,
               tuple(self.memo.interval_token(interval) for interval in temporal),
               tuple(self.memo.interval_token(interval) for interval in active))
        return key, hit, temporal, active

    def prepare(self, hits: Iterable[BattleAnalysisHit]) -> None:
        """Submit complete unique projections before the caller's per-hit replay loop."""
        self.prepare_many(((self, hits),))

    @staticmethod
    def prepare_many(groups: Iterable[tuple[BattleHitBuffProjectionCache, Iterable[BattleAnalysisHit]]]) -> None:
        """Share one native rule evaluation table across the frozen candidate caches."""
        groups = tuple(groups)
        if not groups:
            return
        memo = groups[0][0].memo
        if any(cache.memo is not memo for cache, _ in groups):
            raise ValueError("Candidate projections must share one frozen request memo")
        native = memo.native
        if native is None:
            return
        if native.supports_plan:
            BattleHitBuffProjectionCache._prepare_plans(groups)
            return
        pending = {}
        for cache, hits in groups:
            for ordinal, hit in enumerate(hits):
                if ordinal % 64 == 0 and native.checkpoint is not None:
                    native.checkpoint()
                if id(hit) in cache._prepared:
                    continue
                owner_key = (memo.hit_token(hit), hit.relative_time_us)
                projection = cache._prepared_keys.get(owner_key)
                if projection is not None:
                    cache._remember(hit, projection, prepared=True)
                    continue
                value = cache._input(hit, hit_token=owner_key[0])
                key = value[0]
                projection = memo.projection(key)
                if projection is not None:
                    cache._prepared_keys[owner_key] = projection
                    cache._remember(hit, projection, prepared=True)
                    continue
                if key not in pending:
                    pending[key] = (value, [])
                pending[key][1].append((cache, hit, owner_key))
        rows = list(pending.values())
        for start in range(0, len(rows), 100_000):
            batch = rows[start:start + 100_000]
            projections = native.project([row[0] for row in batch])
            for (value, aliases), projection in zip(batch, projections, strict=True):
                memo.remember_projection(value[0], projection)
                for cache, hit, owner_key in aliases:
                    cache._prepared_keys[owner_key] = projection
                    cache._remember(hit, projection, prepared=True)

    @staticmethod
    def _prepare_plans(groups: tuple[tuple[BattleHitBuffProjectionCache, Iterable[BattleAnalysisHit]], ...]) -> None:
        memo = groups[0][0].memo
        native = memo.native
        if native is None:
            raise ValueError("Projection plans require a native backend")
        pending = {}
        for cache, hits in groups:
            set_token, tokens = memo.interval_set(cache._intervals)
            for ordinal, hit in enumerate(hits):
                if ordinal % 64 == 0 and native.checkpoint is not None:
                    native.checkpoint()
                if id(hit) in cache._prepared:
                    continue
                owner_key = (memo.hit_token(hit), hit.relative_time_us)
                projection = cache._prepared_keys.get(owner_key)
                key = (*owner_key, set_token)
                memo_key = ("native_plan", *key)
                if projection is None:
                    projection = memo.projection(memo_key)
                if projection is not None:
                    cache._prepared_keys[owner_key] = projection
                    cache._remember(hit, projection, prepared=True)
                    continue
                if key not in pending:
                    pending[key] = ((key, hit, cache._intervals.intervals, tokens), [])
                pending[key][1].append((cache, hit, owner_key))
        rows = list(pending.values())
        for start in range(0, len(rows), 100_000):
            batch = rows[start:start + 100_000]
            projections = native.project_plan([row[0] for row in batch])
            for (value, aliases), projection in zip(batch, projections, strict=True):
                memo.remember_projection(("native_plan", *value[0]), projection)
                for cache, hit, owner_key in aliases:
                    cache._prepared_keys[owner_key] = projection
                    cache._remember(hit, projection, prepared=True)

    def _remember(self, hit: BattleAnalysisHit, projection: BattleHitBuffProjection, *,
                  prepared: bool = False) -> BattleHitBuffProjection:
        result = projection if projection.event_id == hit.event_id else replace(projection, event_id=hit.event_id)
        if prepared:
            # Retain the whole prepared axis until this cache's owner finishes consuming
            # it; otherwise >4096 hits would turn a batch into thousands of child calls.
            self._prepared[id(hit)] = (hit, result)
            return result
        self._by_hit_identity[id(hit)] = (hit, result)
        self._by_hit_identity.move_to_end(id(hit))
        if len(self._by_hit_identity) > 4096:
            self._by_hit_identity.popitem(last=False)
        return result

    def project(self, hit: BattleAnalysisHit) -> BattleHitBuffProjection:
        prepared = self._prepared.get(id(hit))
        if prepared is not None:
            return prepared[1]
        remembered = self._by_hit_identity.get(id(hit))
        if remembered is not None:
            self._by_hit_identity.move_to_end(id(hit))
            return remembered[1]
        owner_key = (self.memo.hit_token(hit), hit.relative_time_us)
        projection = self._prepared_keys.get(owner_key)
        if projection is not None:
            return self._remember(hit, projection)
        if self.memo.native is not None and self.memo.native.supports_plan:
            self.prepare((hit,))
            return self._prepared[id(hit)][1]
        key, _, temporal, active = self._input(hit, hit_token=owner_key[0])
        projection = self.memo.projection(key)
        if projection is None and self.memo.native is not None:
            projection = self.memo.native.project([(key, hit, temporal, active)])[0]
            self.memo.remember_projection(key, projection)
        elif projection is None:
            projection = BattleBuffAttributeProjectionService.project_hit(
                hit,
                self._intervals,
                active_intervals=active,
                temporal_intervals=temporal,
                projection_memo=self.memo,
            )
            self.memo.remember_projection(key, projection)
        return self._remember(hit, projection)


__all__ = ["BattleHitBuffProjectionCache"]
