# 单次角色计算显式拥有分析客户端、冻结静态来源和有界数值缓存，退出即释放。
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from src.integrations.analysis_core.analysis_core_release import create_bundled_analysis_client
from src.integrations.analysis_core.nte_analysis_core import NativeAnalysisError
from src.integrations.static_catalog.static_catalog_release import static_catalog_read_unit
from src.domain.catalog.static_catalog import StaticCatalogRelease
from src.services.damage.damage_calculation_service import DamageCalculationService


class RoleDamageCalculator:
    def __init__(self, client=None, *, checkpoint=None, values=(), frozen=False):
        self.client, self.checkpoint = client, checkpoint
        self._values = dict(values)
        self._frozen = frozen

    def calculate(self, inputs):
        if self.client is not None:
            self.client._checkpoint(self.checkpoint)
        elif self.checkpoint is not None:
            self.checkpoint()
        inputs = tuple(inputs)
        pending = tuple(dict.fromkeys(row for row in inputs if row not in self._values))
        if pending:
            if self._frozen:
                raise NativeAnalysisError("独立分析核心响应版本或数据集不匹配")
            values = (self.client.calculate_direct_batch(pending, checkpoint=self.checkpoint) if self.client is not None
                      else tuple(DamageCalculationService.calculate_direct(row) for row in pending))
            # 有界缓存只属于本次冻结 owner，不用对象地址作为身份，不跨角色页或账号存活。
            current = dict(zip(pending, values, strict=True))
            result = tuple(self._values.get(row) or current[row] for row in inputs)
            if len(self._values) + len(pending) > 4096:
                self._values.clear()
            self._values.update(current if len(current) <= 4096 else {})
            return result
        return tuple(self._values[row] for row in inputs)

    def totals(self, groups):
        groups = tuple(groups)
        flat = tuple(item for group in groups for item in group)
        results = self.calculate(flat)
        index, totals = 0, []
        for group in groups:
            totals.append(sum(row.damage for row in results[index:index + len(group)]))
            index += len(group)
        return tuple(totals)


@dataclass(frozen=True)
class RoleDamageValues:
    """单次后台任务已验收的数值；只随当前编辑器持有，不含客户端、句柄或数据库连接。"""
    release: StaticCatalogRelease
    values: tuple

    def calculator(self):
        return RoleDamageCalculator(values=self.values, frozen=True)


@contextmanager
def role_damage_calculator(detail, calculator=None, *, checkpoint=None, static_read_unit=None):
    if calculator is not None:
        yield calculator
        return
    release = detail.get("static_release")
    # 无 runtime 来源的纯值/离线调用保留原公式作差分参考；正式角色页有 token，缺组件不回退。
    if not isinstance(release, StaticCatalogRelease):
        if "static_release" in detail:
            raise NativeAnalysisError("独立分析核心响应版本或数据集不匹配")
        yield RoleDamageCalculator(checkpoint=checkpoint)
        return
    snapshot = detail.get("_role_damage_values")
    if isinstance(snapshot, RoleDamageValues) and static_read_unit is None:
        if snapshot.release != release:
            raise NativeAnalysisError("独立分析核心响应版本或数据集不匹配")
        yield snapshot.calculator()
        return
    if checkpoint is not None:
        checkpoint()
    path = detail.get("static_database_path")
    with static_catalog_read_unit(path, expected_schema_version=release.schema_version,
                                  expected_release=release, read_unit=static_read_unit) as unit:
        client = create_bundled_analysis_client(static_database_path=unit.release.database_path)
        if client is None or not client.supports_direct_damage or client.dataset_version != release.dataset_id:
            raise NativeAnalysisError("独立分析核心响应版本或数据集不匹配")
        with client.verified_session(checkpoint=checkpoint):
            yield RoleDamageCalculator(client, checkpoint=checkpoint)
        unit.ensure_unchanged()
