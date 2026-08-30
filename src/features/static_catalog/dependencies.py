# 组合游戏资料库所有已审计的只读领域提供器。
"""Single composition root for the public game-data catalog."""

from __future__ import annotations

from pathlib import Path

from src.features.static_catalog.contracts import StaticCatalogProvider
from src.features.static_catalog.providers.character import CharacterCatalogProvider
from src.features.static_catalog.providers.fork import ForkCatalogProvider
from src.features.static_catalog.providers.formula_provider import (
    StaticCatalogCounterfactualProvider,
    StaticCatalogFormulaProvider,
)
from src.features.static_catalog.providers.misc_provider import StaticCatalogMiscProvider
from src.features.static_catalog.providers.monster_provider import StaticCatalogMonsterProvider
from src.features.static_catalog.providers.overview import StaticCatalogOverviewProvider


def build_static_catalog_providers(
    database_path: str | Path,
) -> tuple[StaticCatalogProvider, ...]:
    """Build each narrow provider against one immutable release database path."""

    path = Path(database_path).resolve()
    manifest_path = path.with_name("manifest.json")
    misc = tuple(
        StaticCatalogMiscProvider(path, manifest_path, domain_key=key)
        for key in ("equipment", "skills", "effects", "assets", "sources")
    )
    return (
        StaticCatalogOverviewProvider(str(path)),
        CharacterCatalogProvider(path),
        ForkCatalogProvider(path),
        StaticCatalogMonsterProvider(path),
        *misc,
        StaticCatalogFormulaProvider(path),
        StaticCatalogCounterfactualProvider(path),
    )
