# 怪物与玩法页面的只读目录控制器。
"""Own cached public Service projections without leaking DAO access to the view."""

from __future__ import annotations

from src.services.static_catalog_monster_models import (
    CatalogDetail,
    CatalogEntry,
    CatalogFilter,
    FeastPeriod,
    FeastSetup,
)
from src.services.static_catalog_monster_service import StaticCatalogMonsterService


class MonsterCatalogPageController:
    def __init__(self, service: StaticCatalogMonsterService) -> None:
        self._service = service
        self._entries: dict[str, tuple[CatalogEntry, ...]] = {}
        self._details: dict[str, CatalogDetail | None] = {}
        self._family_profiles: dict[str, tuple[str, ...]] = {}

    def entries_for(self, play_mode: str) -> tuple[CatalogEntry, ...]:
        if play_mode not in self._entries:
            rows: list[CatalogEntry] = []
            offset = 0
            while True:
                page = self._service.list_entries(CatalogFilter(
                    play_mode=play_mode, page_size=200, offset=offset,
                ))
                rows.extend(page.items)
                if not page.has_more:
                    break
                offset += len(page.items)
            if play_mode == "high_risk":
                rows = [row for row in rows if self._has_formal_pool(row)]
            self._entries[play_mode] = tuple(rows)
        return self._entries[play_mode]

    def detail(self, key: str) -> CatalogDetail | None:
        if key not in self._details:
            self._details[key] = self._service.get_detail(key)
        return self._details[key]

    @staticmethod
    def value(detail: CatalogDetail, label: str) -> str | None:
        for section in detail.sections:
            for value in section.values:
                if value.label == label:
                    return value.value
        return None

    def _has_formal_pool(self, entry: CatalogEntry) -> bool:
        detail = self.detail(entry.key)
        value = self.value(detail, "逐难度怪物池") if detail else None
        return bool(value and value != "不可用")

    def outer_rotations(self) -> tuple[CatalogEntry, ...]:
        representative: dict[str, CatalogEntry] = {}
        for entry in self.entries_for("outer_realm"):
            representative.setdefault(entry.primary_id, entry)

        def ordinal(entry: CatalogEntry) -> int:
            tail = entry.primary_id.rsplit("_", 1)[-1]
            return int(tail) if tail.isdigit() else -1

        current = sorted(
            (row for row in representative.values() if row.release_state == "current"),
            key=ordinal,
        )
        upcoming = sorted(
            (
                row for row in representative.values()
                if row.release_state in {"next", "scheduled"}
            ),
            key=ordinal,
        )
        history = sorted(
            (row for row in representative.values() if row.release_state == "historical"),
            key=ordinal,
            reverse=True,
        )
        unscheduled = sorted(
            (row for row in representative.values() if row.release_state == "unscheduled"),
            key=ordinal,
        )
        return tuple((*current, *upcoming, *history, *unscheduled))

    def outer_buff(self, config_id: str) -> CatalogDetail | None:
        return self.detail(f"outer_buff|{config_id}")

    def witch_blessings(self) -> tuple[CatalogEntry, ...]:
        return self._service.list_witch_blessings()

    def profile_family_keys(self, monster_id: str) -> tuple[str, ...]:
        if monster_id not in self._family_profiles:
            self._family_profiles[monster_id] = self._service.profile_family_keys(
                monster_id
            )
        return self._family_profiles[monster_id]

    def feast_periods(self) -> tuple[FeastPeriod, ...]:
        return self._service.list_feast_periods()

    def feast_setup(
        self, period_id: str, stage_id: str,
    ) -> FeastSetup | None:
        return self._service.get_feast_setup(period_id, stage_id)

    def feast_detail(
        self,
        period_id: str,
        stage_id: str,
        difficulty_id: int,
        selected_option_ids: tuple[str, ...],
    ) -> CatalogDetail | None:
        return self._service.get_feast_detail(
            period_id,
            stage_id,
            difficulty_id,
            selected_option_ids=selected_option_ids,
        )
