# 仅复用同一养成任务的整数模型结构，不保留材料需求、库存或求解结果。
from __future__ import annotations

from collections import OrderedDict


class StaminaModelPreparation:
    """有界任务 owner；矩阵按材料/副本顺序、精确产出和转换边完整区分。"""

    def __init__(self, *, maximum_entries=32, maximum_bytes=8 * 1024 * 1024, minimum_coefficients=512):
        self._models = OrderedDict()
        self.minimum_coefficients = minimum_coefficients
        self.maximum_entries = maximum_entries
        self.maximum_bytes = maximum_bytes
        self.size_bytes = 0
        self.build_count = 0
        self.hit_count = 0

    def prepare(self, ids, stages, edges=()):
        import numpy as np
        # 类型也进入键，True/1、整数/浮点来源不会隐式混同；顺序维持原求解器同分次序。
        count = len(stages) + len(edges)
        cacheable = self.maximum_entries > 0 and len(ids) * count >= self.minimum_coefficients
        key = (tuple(ids), tuple((type(stage.stamina_cost), stage.stamina_cost,
            tuple((item.item_id, type(item.quantity), item.quantity) for item in stage.yields))
            for stage in stages), tuple(edges)) if cacheable else None
        if key in self._models:
            self.hit_count += 1
            self._models.move_to_end(key)
            return self._models[key]
        matrix = np.zeros((len(ids), count), dtype=float)
        index = {item_id: row for row, item_id in enumerate(ids)}
        for column, stage in enumerate(stages):
            for item in stage.yields:
                if item.item_id in index:
                    matrix[index[item.item_id], column] += item.quantity
        for offset, (lower, higher) in enumerate(edges, start=len(stages)):
            matrix[index[lower], offset] -= 3
            matrix[index[higher], offset] += 1
        cost = np.asarray([stage.stamina_cost for stage in stages] + [0] * len(edges), dtype=float)
        secondary = np.asarray([1] * len(stages) + [0] * len(edges), dtype=float)
        model = (matrix, cost, secondary)
        # 小模型实测统一缓存有负收益：不构键、不驻留，仍按原双阶段求解。
        # 大模型 key 只含静态结构；保守计入 ID/键开销，超限仍精确构建但不驻留。
        size = sum(value.nbytes for value in model) + (len(repr(key).encode("utf8")) * 4 if cacheable else 0)
        self.build_count += 1
        if cacheable and size <= self.maximum_bytes:
            while self._models and (len(self._models) >= self.maximum_entries
                    or self.size_bytes + size > self.maximum_bytes):
                old = self._models.popitem(last=False)[1]
                self.size_bytes -= old[3]
            stored = (*model, size)
            self._models[key] = stored
            self.size_bytes += size
        return (*model, size)
