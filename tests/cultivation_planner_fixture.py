# 为养成界面测试提供材料准备协议，避免把简化测试桩作为生产兼容入口。
from __future__ import annotations

from src.services.progression.character_requirements import MaterialSummaryStatus
from src.services.progression.models import CultivationPlan, CultivationPreparedTarget
from src.services.progression.stamina_planner import stamina_material_ids


class PlanningFixture:
    def calculate(self, _request):
        return CultivationPlan("测试角色", MaterialSummaryStatus.COMPLETE, (), (), 0, 0, (), ())

    def load_farming_stages(self):
        return ()

    def prepare(self, request):
        stages = self.load_farming_stages()
        return CultivationPreparedTarget(request, self.calculate(request), stages, stamina_material_ids(stages))

    def calculate_stamina(self, _plan, **_kwargs):
        return None
