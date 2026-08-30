# 组合游戏资料库所有已审计的只读领域提供器。
"""Single composition root for the public game-data catalog."""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from src.features.static_catalog.contracts import StaticCatalogProvider
from src.features.static_catalog.domain_pages import build_character_catalog_page
from src.features.static_catalog.domain_pages.combat_mechanics_page import (
    CombatMechanicsCatalogPage,
    build_combat_mechanics_catalog_page,
)
from src.features.static_catalog.domain_pages.equipment_page import (
    EquipmentCatalogPage,
    build_equipment_catalog_page,
)
from src.features.static_catalog.domain_pages.fork_page import (
    ForkCatalogPage,
    build_fork_catalog_page,
)
from src.features.static_catalog.domain_pages.monster_page import (
    build_monster_catalog_page,
)
from src.features.static_catalog.page import StaticCatalogDomainPageSpec
from src.features.static_catalog.progression_calculator import (
    ProgressionCalculatorDialog,
    build_progression_calculator_dialog,
)
from src.features.static_catalog.progression_calculator_models import (
    ProgressionCalculatorOutcome,
)
from src.features.static_catalog.providers.character import CharacterCatalogProvider
from src.features.static_catalog.providers.fork import ForkCatalogProvider
from src.features.static_catalog.providers.formula_provider import (
    StaticCatalogCounterfactualProvider,
    StaticCatalogFormulaProvider,
)
from src.features.static_catalog.providers.misc_provider import StaticCatalogMiscProvider
from src.features.static_catalog.providers.monster_provider import StaticCatalogMonsterProvider
from src.features.static_catalog.providers.overview import StaticCatalogOverviewProvider
from src.services.static_catalog_character_service import StaticCatalogCharacterService
from src.services.static_catalog_character_release_metadata import (
    CharacterReleaseMetadataService,
)
from src.services.static_catalog_monster_service import StaticCatalogMonsterService
from src.services.progression_stamina_service import ProgressionStaminaService
from src.services.static_catalog_terminology_service import (
    StaticCatalogTerminologyService,
)
from src.storage.sqlite.static_catalog_character_queries import (
    StaticCatalogCharacterQueries,
)
from src.storage.sqlite.static_game_data_dao import StaticGameDataDao
from src.ui.equipment_presentation import EquipmentPresentation


InventorySnapshotLoader = Callable[[], tuple[str, int, Mapping[str, Any]]]
_LOGGER = logging.getLogger(__name__)


def _close_owned_components(
    callbacks: tuple[Callable[[], None], ...],
    *,
    label: str,
) -> None:
    errors: list[Exception] = []
    for callback in callbacks:
        try:
            callback()
        except Exception as exc:
            errors.append(exc)
    if errors:
        raise RuntimeError(
            f"关闭{label}时有 {len(errors)} 个组件失败"
        ) from errors[0]


def build_static_catalog_providers(
    database_path: str | Path,
) -> tuple[StaticCatalogProvider, ...]:
    """Build each narrow provider against one immutable release database path."""

    path = Path(database_path).resolve()
    manifest_path = path.with_name("manifest.json")
    terminology_dao = StaticGameDataDao(path)
    terminology = StaticCatalogTerminologyService(terminology_dao)
    providers: list[StaticCatalogProvider] = []
    try:
        overview = StaticCatalogOverviewProvider(str(path))
        character = CharacterCatalogProvider(path)
        fork = ForkCatalogProvider(path)
        providers.extend((overview, character, fork))
        misc_items: list[StaticCatalogProvider] = []
        for key in ("equipment", "skills", "effects", "assets", "sources"):
            provider = StaticCatalogMiscProvider(path, manifest_path, domain_key=key)
            misc_items.append(provider)
            providers.append(provider)
        misc = tuple(misc_items)
        formula = StaticCatalogFormulaProvider(path)
        counterfactual = StaticCatalogCounterfactualProvider(path)
        providers.extend((formula, counterfactual))
        monster_provider = StaticCatalogMonsterProvider(
            path,
            terminology_service=terminology,
            close_callbacks=(terminology_dao.close,),
        )
        providers.append(monster_provider)
        return (
            overview,
            character,
            fork,
            monster_provider,
            *misc,
            formula,
            counterfactual,
        )
    except Exception:
        for provider in reversed(providers):
            try:
                provider.close()
            except Exception:
                pass
        terminology_dao.close()
        raise


def build_static_catalog_domain_pages(
    database_path: str | Path,
    game_ui_asset_root: str | Path,
    *,
    equipment_presentation: EquipmentPresentation,
    equipment_inventory_loader: InventorySnapshotLoader | None = None,
    open_catalog_link: Callable[[Any], None] | None = None,
) -> tuple[StaticCatalogDomainPageSpec, ...]:
    """Build owned UI registrations without exposing DAO lifetime to MainWindow."""

    path = Path(database_path).resolve()
    terminology_dao = StaticGameDataDao(path)
    terminology = StaticCatalogTerminologyService(terminology_dao)
    queries: StaticCatalogCharacterQueries | None = None
    monster_service: StaticCatalogMonsterService | None = None
    try:
        queries = StaticCatalogCharacterQueries(path)
        service = StaticCatalogCharacterService(queries)
        release_metadata = CharacterReleaseMetadataService(queries, terminology)
        progression_service = ProgressionStaminaService(
            official_stage_source=terminology_dao,
        )
        monster_service = StaticCatalogMonsterService.from_database(
            path,
            terminology_service=terminology,
        )
    except Exception:
        if monster_service is not None:
            monster_service.close()
        if queries is not None:
            queries.close()
        terminology_dao.close()
        raise
    assert queries is not None
    assert monster_service is not None
    character_dialogs: list[ProgressionCalculatorDialog] = []
    fork_pages: list[ForkCatalogPage] = []
    fork_dialogs: list[ProgressionCalculatorDialog] = []
    equipment_pages: list[EquipmentCatalogPage] = []
    mechanics_pages: list[CombatMechanicsCatalogPage] = []
    asset_root = Path(game_ui_asset_root).resolve()

    def open_progression_request(
        dialog: ProgressionCalculatorDialog,
        request: object,
        on_result: Callable[[ProgressionCalculatorOutcome], None],
    ) -> None:
        """Keep malformed page requests from escaping a Qt signal callback."""

        try:
            dialog.open_request(request, on_result=on_result)
        except Exception as exc:
            try:
                dialog.dispose()
            except Exception as close_exc:
                _LOGGER.error(
                    "static catalog progression dialog cleanup failed",
                    extra={"error_type": type(close_exc).__name__},
                )
            _LOGGER.error(
                "static catalog progression request rejected",
                extra={
                    "request_type": type(request).__name__,
                    "error_type": type(exc).__name__,
                },
            )

    def build_character_page(parent):
        page = build_character_catalog_page(
            service=service,
            release_metadata_service=release_metadata,
            game_ui_asset_root=asset_root,
            terminology_service=terminology,
            open_catalog_link=open_catalog_link,
            parent=parent,
        )
        dialog: ProgressionCalculatorDialog | None = None
        try:
            dialog = build_progression_calculator_dialog(
                service=progression_service,
                terminology_service=terminology,
                parent=page,
            )

            def project(outcome: ProgressionCalculatorOutcome) -> None:
                page.set_progression_result(
                    target=outcome.kind,
                    character_id=int(outcome.owner_id),
                    skill_id=outcome.skill_id,
                    result=outcome.result,
                )

            page.progression_requested.connect(
                lambda request: open_progression_request(dialog, request, project)
            )
            character_dialogs.append(dialog)
        except Exception:
            if dialog is not None:
                dialog.dispose()
            page.deleteLater()
            raise
        return page

    def close_character_pages() -> None:
        callbacks = tuple(dialog.dispose for dialog in character_dialogs)
        character_dialogs.clear()
        _close_owned_components(
            (*callbacks, queries.close),
            label="角色图鉴",
        )

    def build_fork_page(parent):
        page = build_fork_catalog_page(
            database_path=path,
            game_ui_asset_root=asset_root,
            terminology_service=terminology,
            parent=parent,
        )
        dialog: ProgressionCalculatorDialog | None = None
        try:
            if open_catalog_link is not None:
                page.catalog_link_requested.connect(open_catalog_link)
            dialog = build_progression_calculator_dialog(
                service=progression_service,
                terminology_service=terminology,
                parent=page,
            )

            def project(outcome: ProgressionCalculatorOutcome) -> None:
                page.set_progression_result(
                    fork_id=outcome.owner_id,
                    result=outcome.result,
                )

            page.progression_requested.connect(
                lambda request: open_progression_request(dialog, request, project)
            )
            fork_pages.append(page)
            fork_dialogs.append(dialog)
        except Exception:
            if dialog is not None:
                dialog.dispose()
            page.dispose()
            raise
        return page

    def close_fork_pages() -> None:
        callbacks = tuple(dialog.dispose for dialog in fork_dialogs)
        fork_dialogs.clear()
        callbacks += tuple(page.dispose for page in fork_pages)
        fork_pages.clear()
        _close_owned_components(callbacks, label="弧盘图鉴")

    def load_equipment_inventory(page: EquipmentCatalogPage) -> None:
        page.invalidate_inventory_projection()
        if equipment_inventory_loader is None:
            return
        account_id, generation, snapshot = equipment_inventory_loader()
        page.apply_inventory_snapshot(
            account_id=account_id,
            generation=generation,
            snapshot=snapshot,
        )

    def build_equipment_page(parent):
        page = build_equipment_catalog_page(
            database_path=path,
            game_ui_asset_root=asset_root,
            presentation=equipment_presentation,
            terminology_service=terminology,
            open_catalog_link=open_catalog_link,
            parent=parent,
        )
        try:
            load_equipment_inventory(page)
        except Exception:
            page.deleteLater()
            raise
        equipment_pages.append(page)
        return page

    def refresh_equipment_pages() -> None:
        pages = tuple(equipment_pages)
        for page in pages:
            page.invalidate_inventory_projection()
        if equipment_inventory_loader is None:
            return
        for page in pages:
            account_id, generation, snapshot = equipment_inventory_loader()
            page.apply_inventory_snapshot(
                account_id=account_id,
                generation=generation,
                snapshot=snapshot,
            )

    def close_equipment_pages() -> None:
        equipment_pages.clear()

    def build_mechanics_page(parent):
        page = build_combat_mechanics_catalog_page(
            database_path=path,
            game_ui_asset_root=asset_root,
            open_catalog_link=open_catalog_link or (lambda _link: None),
            terminology_service=terminology,
            parent=parent,
        )
        mechanics_pages.append(page)
        return page

    def close_mechanics_pages_and_terminology() -> None:
        callbacks = tuple(page.dispose for page in mechanics_pages)
        mechanics_pages.clear()
        _close_owned_components(
            (*callbacks, terminology_dao.close),
            label="战斗机制图鉴与公共术语",
        )

    specs = [
        StaticCatalogDomainPageSpec(
            domain_key="character",
            title="角色图鉴",
            build=build_character_page,
            close=close_character_pages,
        ),
        StaticCatalogDomainPageSpec(
            domain_key="fork",
            title="弧盘图鉴",
            build=build_fork_page,
            close=close_fork_pages,
        ),
    ]
    specs.append(StaticCatalogDomainPageSpec(
        domain_key="equipment",
        title="空幕与驱动",
        build=build_equipment_page,
        close=close_equipment_pages,
        refresh=refresh_equipment_pages,
    ))
    specs.append(StaticCatalogDomainPageSpec(
        domain_key="monsters",
        title="怪物与玩法",
        build=lambda parent: build_monster_catalog_page(
            service=monster_service,
            terminology_service=terminology,
            game_ui_asset_root=asset_root,
            open_catalog_link=open_catalog_link,
            parent=parent,
        ),
        close=monster_service.close,
    ))
    specs.append(StaticCatalogDomainPageSpec(
        domain_key="combat_mechanics",
        title="战斗机制图鉴",
        build=build_mechanics_page,
        close=close_mechanics_pages_and_terminology,
    ))
    return tuple(specs)
