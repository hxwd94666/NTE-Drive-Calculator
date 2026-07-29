# 在冻结账号代次下生成角色图纸并丢弃跨账号失效结果。
"""Controller boundary for static blueprint generation."""

from __future__ import annotations

from src.features.blueprints.dependencies import BlueprintDependencies
from src.observability import OperationContext, log_event, operation_scope
from src.services.blueprint_service import solve_blueprints_from_static
from src.storage.sqlite.static_game_data_dao import StaticGameDataDao


class BlueprintController:
    def __init__(self, dependencies: BlueprintDependencies) -> None:
        self.dependencies = dependencies
        self.operation_context = OperationContext.create(
            "blueprint",
            account_id=dependencies.account_id,
            context_generation=dependencies.generation,
        )

    def generate(self) -> dict[str, dict]:
        with operation_scope(
            self.operation_context,
            started_event="blueprint.generate_started",
            succeeded_event="blueprint.generate_succeeded",
            failed_event="blueprint.generate_failed",
            message="生成角色图纸",
        ) as span:
            with StaticGameDataDao() as static_dao:
                results = solve_blueprints_from_static(static_dao)
            span.annotate(
                role_count=len(results),
                plan_count=sum(
                    len(item.get("blueprints") or ())
                    for item in results.values()
                ),
            )
            return results

    def accepts(self, current: BlueprintDependencies) -> bool:
        accepted = current == self.dependencies
        if not accepted:
            log_event(
                "WARNING",
                "blueprint.stale_result_discarded",
                "丢弃账号切换前的角色图纸结果",
                self.operation_context,
                current_account_id=current.account_id,
                current_context_generation=current.generation,
            )
        return accepted

