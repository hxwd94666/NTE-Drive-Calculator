# 游戏资料库争锋赏宴按活动期浏览的页面行为。
"""Feast period and challenge browsing mixed into the monster archive page."""

from __future__ import annotations

from src.domain.localization.catalog import localized_text
from src.ui.common.enum_text import ui_enum_text
from pathlib import Path

from src.features.static_catalog.domain_pages.monsters.browse_models import (
    PLAY_LABELS,
    BrowseCard,
    BrowseSection,
    BrowseState,
)
from src.services.static_catalog.monsters.models import FeastPeriod, FeastSetup


class FeastCatalogBrowserMixin:
    """Render period cards first, then the period's ordered challenges."""

    def _feast_state(self) -> BrowseState:
        periods = self._controller.feast_periods()
        active = tuple(
            self._feast_period_card(period) for period in periods
            if period.release_state in {"current", "next", "scheduled"}
        )
        history = tuple(
            self._feast_period_card(period) for period in periods
            if period.release_state == "historical"
        )
        unscheduled = tuple(
            self._feast_period_card(period) for period in periods
            if period.release_state == "unscheduled"
        )
        text = self.text_catalog.render
        sections = []
        if active:
            sections.append(BrowseSection(
                "当前与预计",
                "按大陆服公开排期判定；进入活动期后再选择挑战对象。",
                active,
            ))
        if history:
            sections.append(BrowseSection(
                "往期",
                "保留同期正式挑战成员；数值读取发行资源中仍可验证的配置。",
                history,
            ))
        if unscheduled:
            sections.append(BrowseSection(
                text("catalog.feast.unscheduled_section"),
                text("catalog.feast.unscheduled_note"), unscheduled,
            ))
        return BrowseState(
            ui_enum_text(getattr(self, 'text_catalog', None), PLAY_LABELS)["feast"],
            text("catalog.feast.browse_description"),
            tuple(sections),
        )

    def _feast_period_card(self, period: FeastPeriod) -> BrowseCard:
        setup = (
            self._controller.feast_setup(period.period_id, period.challenge_ids[0])
            if period.challenge_ids else None
        )
        text = self.text_catalog.render
        unknown = period.release_state == "unscheduled"
        state_label = {
            "current": text("catalog.feast.current_badge"),
            "next": text("catalog.feast.upcoming_badge"), "scheduled": text("catalog.feast.upcoming_badge"),
            "historical": text("catalog.feast.historical_badge"),
            "unscheduled": text("catalog.feast.unscheduled_badge"),
        }
        return BrowseCard(
            text("catalog.feast.source_title") if unknown else period.display_label,
            (text("catalog.feast.source_summary", count=len(period.challenge_ids)) if unknown
             else text("catalog.feast.period_summary", count=len(period.challenge_ids), schedule=period.schedule_label)),
            state_label.get(period.release_state, period.release_state),
            self._feast_setup_icon(setup),
            lambda checked=False, value=period: self._open_feast_period(value),
            formal_id=period.period_id,
            category=state_label.get(period.release_state, ""),
            period=period.display_label,
        )

    def _open_feast_period(self, period: FeastPeriod) -> None:
        cards = []
        for stage_id in period.challenge_ids:
            setup = self._controller.feast_setup(period.period_id, stage_id)
            if setup is None:
                continue
            cards.append(BrowseCard(
                localized_text(getattr(self, 'text_catalog', None), 'auto.features.static_catalog.domain_pages.monsters.feast_browser.open_feast_period.fe83e50d4', '挑战 {challenge_ordinal} · {title}', challenge_ordinal=f'{setup.challenge_ordinal}', title=f'{setup.title}'),
                localized_text(getattr(self, 'text_catalog', None), 'auto.features.static_catalog.domain_pages.monsters.feast_browser.open_feast_period.f252ba932', '{boss_name} · {count} 个难度', boss_name=f'{setup.boss_name}', count=f'{len(setup.difficulties)}'),
                "挑战对象",
                self._feast_setup_icon(setup),
                lambda checked=False, value=setup: self._open_feast_stage(value),
                formal_id=stage_id,
                period=period.display_label,
            ))
        text = self.text_catalog.render
        unknown = period.release_state == "unscheduled"
        self._show_state(BrowseState(
            text("catalog.feast.source_heading") if unknown else localized_text(getattr(self, 'text_catalog', None), 'auto.features.static_catalog.domain_pages.monsters.feast_browser.open_feast_period.fa6a4c729', '争锋赏宴 · {display_label}', display_label=f'{period.display_label}'),
            text("catalog.feast.source_description") if unknown else localized_text(getattr(self, 'text_catalog', None), 'auto.features.static_catalog.domain_pages.monsters.feast_browser.open_feast_period.fd67748cd', '{schedule_label} · 选择挑战后查看难度、条件与敌方画像。', schedule_label=f'{period.schedule_label}'),
            (BrowseSection(
                "挑战对象", (text("catalog.feast.source_challenges_note", count=len(cards)) if unknown
                             else localized_text(getattr(self, 'text_catalog', None), 'auto.features.static_catalog.domain_pages.monsters.feast_browser.open_feast_period.f8ac36841', '共 {count} 个，按当期正式顺序展示。', count=f'{len(cards)}')),
                tuple(cards),
            ),),
        ), push=True)

    def _feast_setup_icon(self, setup: FeastSetup | None) -> Path | None:
        if setup is None:
            return None
        detail = self._controller.feast_detail(
            setup.period_id, setup.stage_id, setup.default_difficulty_id, ()
        )
        return self._formal_icon(detail)

    def _open_feast_stage(self, setup: FeastSetup) -> None:
        self.feast_view.set_stage(
            setup,
            icon=self._feast_setup_icon(setup),
            loader=self._controller.feast_detail,
            blessings=self._controller.witch_blessings(),
            blessing_loader=self._controller.detail,
        )
        self.stack.setCurrentWidget(self.feast_view)
        self._catalog_navigation_listener()

    def _feast_home_badge(self) -> str:
        periods = self._controller.feast_periods()
        source_count = sum(period.release_state == "unscheduled" for period in periods)
        period_count = len(periods) - source_count
        text = self.text_catalog.render
        if not period_count:
            return text("catalog.feast.source_home_count", count=source_count)
        if not source_count:
            return text("catalog.feast.period_home_count", count=period_count)
        return text("catalog.feast.mixed_home_count", periods=period_count, sources=source_count)
