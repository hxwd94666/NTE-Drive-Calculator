# 合并单次重放中的唯一原生直伤作业并按原轴回填。
from __future__ import annotations
from collections.abc import Callable
from typing import Any
from src.domain.battle_report.analysis.backend_contracts import DirectFormulaBackend
from src.services.battle_report.replay.direct_numeric import prepare_direct_formula_input
from src.services.battle_report.replay.formula_projection import project_replay_formula_context
from src.services.battle_report.replay.support import apply_observed_damage_correction, reanchor_direct_replay_result


class DirectReplayBatch:
    """Keep prepared job data local to one immutable replay invocation."""

    def __init__(self, backend: DirectFormulaBackend | None, render: Callable[..., Any]):
        self.backend = backend
        self.render = render
        self.jobs: list[dict[str, Any]] = []
        self.job_by_cache_key: dict[object, int] = {}
        self.pending: list[tuple[Any, ...]] = []

    def add(self, results, cache_key, kwargs, hit, formula_hit, evidence) -> None:
        job_index = None if cache_key is None else self.job_by_cache_key.get(cache_key)
        if job_index is None:
            job_index = len(self.jobs)
            self.jobs.append(kwargs)
            if cache_key is not None:
                self.job_by_cache_key[cache_key] = job_index
        self.pending.append((len(results), job_index, hit, formula_hit, evidence))
        results.append(None)

    # 作业只属于本次冻结轴；按原位置回填并保留取消检查点。
    def finish(self, results, checkpoint: Callable[[], None]) -> None:
        if not self.jobs:
            return
        assert self.backend is not None
        inputs = tuple(prepare_direct_formula_input(**kwargs) for kwargs in self.jobs)
        numeric = self.backend.calculate_batch(inputs, checkpoint=checkpoint)
        if len(numeric) != len(inputs):
            raise ValueError("Native direct formula result count mismatch")
        templates = []
        for index, (kwargs, value) in enumerate(zip(self.jobs, numeric)):
            if index % 64 == 0:
                checkpoint()
            templates.append(self.render(**kwargs, numeric=value))
        used: set[int] = set()
        for ordinal, (position, job_index, hit, formula_hit, evidence) in enumerate(self.pending):
            if ordinal % 64 == 0:
                checkpoint()
            template = templates[job_index]
            result = reanchor_direct_replay_result(template, formula_hit) if job_index in used else template
            used.add(job_index)
            results[position] = apply_observed_damage_correction(
                project_replay_formula_context(result, formula_hit, evidence),
                hit,
            )
