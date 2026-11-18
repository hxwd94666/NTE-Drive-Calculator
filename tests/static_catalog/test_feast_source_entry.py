# 验证未排期争锋来源可浏览、正式身份不变和本轮文案的注入/回退。
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtWidgets import QApplication, QLabel

from src.app.theme import apply_app_theme, current_theme_name
from src.domain.localization.catalog import make_catalog
from src.features.static_catalog.domain_pages.monsters.feast_browser import FeastCatalogBrowserMixin
from src.features.static_catalog.domain_pages.monsters.page import build_monster_catalog_page
from src.integrations.preferences.locale_files import bundled_text_catalog
from src.services.static_catalog.common.terminology import StaticCatalogTerminologyService
from src.services.static_catalog.monsters.models import FeastPeriod
from src.services.static_catalog.monsters.service import StaticCatalogMonsterService
from src.storage.sqlite.static_game_data_dao import StaticGameDataDao

ROOT = Path(__file__).resolve().parents[2]
NTE_TEST_TIER = "core"


@contextmanager
def _page(catalog=None):
    app = QApplication.instance() or QApplication([])
    with StaticGameDataDao(ROOT / "data/game_static.sqlite3") as dao:
        terminology = StaticCatalogTerminologyService(dao)
        service = StaticCatalogMonsterService.from_database(
            ROOT / "data/game_static.sqlite3", terminology_service=terminology
        )
        page = None
        try:
            page = build_monster_catalog_page(
                service=service,
                terminology_service=terminology,
                game_ui_asset_root=ROOT / "data/role_catalog/game_ui",
                catalog=catalog,
            )
            yield app, page, service
        finally:
            if page is not None:
                page.close()
                page.deleteLater()
                QCoreApplication.sendPostedEvents(page, QEvent.DeferredDelete)
            service.close()


@pytest.mark.parametrize("theme", ["black"])
def test_release_source_opens_its_challenges_without_claiming_an_active_period(theme):
    app = QApplication.instance() or QApplication([])
    original = current_theme_name()
    apply_app_theme(app, theme)
    try:
        with _page() as (_app, page, service):
            periods = service.list_feast_periods()
            source = next(period for period in periods if period.release_state == "unscheduled")
            page.open_mode("feast")
            section = next(section for section in page._active_state.sections if section.title == "排期待确认")
            card = next(card for card in section.cards if card.formal_id == source.period_id)
            assert card.badge == "排期待确认" and page._home_badge("feast").endswith("份来源")
            card.action()
            challenges = page._active_state.sections[0].cards
            assert tuple(card.formal_id for card in challenges) == source.challenge_ids
            assert challenges
            selected = challenges[0].formal_id
            challenges[0].action()
            assert page.stack.currentWidget() is page.feast_view
            assert page.feast_view._setup.stage_id == selected
            assert page.feast_view._setup.period_state == "unscheduled"
    finally:
        apply_app_theme(app, original)


def test_partial_patch_is_injected_and_source_text_stays_literal():
    catalog = make_catalog(
        {
            "format_version": 1,
            "catalog_id": "nte-ui",
            "locale": "en-US",
            "display_name": "Test",
            "messages": {"catalog.feast.unscheduled_section": "<b>Pending & source</b>"},
        },
        base=bundled_text_catalog(),
    )
    with _page(catalog) as (app, page, _service):
        assert page.text_catalog is catalog
        page.open_mode("feast")
        page.show()
        app.processEvents()
        section = next(section for section in page._active_state.sections if section.title == "<b>Pending & source</b>")
        assert section.cards[0].badge == "排期待确认"  # 旧补丁缺新 key 仍回退发行中文。
        labels = [label for label in page.findChildren(QLabel) if label.text() == section.title]
        assert labels and all(label.textFormat() == Qt.PlainText for label in labels)
        section.cards[0].action()
        assert "不代表活动已开放" in page._active_state.subtitle


def test_adding_unscheduled_sources_preserves_known_period_sections():
    periods = tuple(
        FeastPeriod(state, state, state, "fixture", (), state, "") for state in ["current", "historical", "unscheduled"]
    )

    class Browser(FeastCatalogBrowserMixin):
        text_catalog = bundled_text_catalog()
        _controller = SimpleNamespace(feast_periods=lambda: periods)

        def _feast_setup_icon(self, _setup):
            return None

    browser = Browser()
    assert browser._feast_home_badge() == "2 期活动 · 1 份待排期资料"
    state = browser._feast_state()
    assert [card.formal_id for section in state.sections for card in section.cards] == [
        "current",
        "historical",
        "unscheduled",
    ]
