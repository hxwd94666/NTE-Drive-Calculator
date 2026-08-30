# 验证游戏资料库弧盘域的只读、投影与界面契约。
"""Public behavior contract for the read-only fork catalog domain."""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import unittest
from pathlib import Path
from typing import Any

from src.services.static_catalog_fork_service import (
    CatalogOrigin,
    CatalogRelation,
    StaticCatalogForkService,
)
from src.storage.sqlite.static_catalog_fork_queries import StaticCatalogForkDao


PROJECT_ROOT = Path(__file__).resolve().parents[1]
STATIC_DATABASE = PROJECT_ROOT / "data" / "game_static.sqlite3"
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


class StaticCatalogForkServiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.service = StaticCatalogForkService.from_database(STATIC_DATABASE)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.service.close()

    def test_metadata_reports_release_provenance_and_importer_gaps(self) -> None:
        metadata = self.service.metadata()

        self.assertEqual(metadata.schema_version, 29)
        self.assertTrue(metadata.dataset_id)
        self.assertGreaterEqual(dict(metadata.counts)["fork_item"], 1)
        self.assertFalse(metadata.has_fork_skill_tables)
        self.assertEqual(metadata.source_payloads_preserved, 0)
        self.assertTrue(any("独立技能表" in note for note in metadata.audit_notes))
        self.assertTrue(any("payload" in note for note in metadata.audit_notes))

    def test_list_is_paginated_and_treats_search_as_literal_text(self) -> None:
        first_page = self.service.list_forks(page=1, page_size=7)
        injection = self.service.list_forks(query="' OR 1=1 --")

        self.assertEqual(len(first_page.items), 7)
        self.assertGreater(first_page.total_pages, 1)
        self.assertEqual(injection.total_items, 0)

    def test_detail_preserves_official_rows_and_marks_critical_states_derived(self) -> None:
        first = self.service.list_forks(page_size=1).items[0]
        detail = self.service.get_fork(first.fork_id)

        self.assertIsNotNone(detail)
        assert detail is not None
        self.assertEqual(len(detail.growth_levels), 80)
        self.assertEqual(len(detail.breakthroughs), 7)
        self.assertEqual(len(detail.refinement_levels), 5)
        self.assertEqual(
            [(state.level, state.state) for state in detail.critical_level_states],
            [
                (level, state)
                for level in (20, 30, 40, 50, 60, 70)
                for state in ("突破前", "突破后")
            ],
        )
        self.assertTrue(all(
            state.origin is CatalogOrigin.DERIVED_DISPLAY
            for state in detail.critical_level_states
        ))
        self.assertTrue(all(
            growth.source.relative_path for growth in detail.growth_levels
        ))
        self.assertTrue(all(
            not growth.source.payload_preserved for growth in detail.growth_levels
        ))

    def test_detail_exposes_copyable_formal_relations_and_resource_paths(self) -> None:
        first = self.service.list_forks(page_size=1).items[0]
        detail = self.service.get_fork(first.fork_id)

        self.assertIsNotNone(detail)
        assert detail is not None
        self.assertTrue(any(resource.kind == "icon" for resource in detail.resources))
        self.assertTrue(any(relation.kind == "buff" for relation in detail.relations))
        self.assertTrue(all(relation.copy_value for relation in detail.relations))
        self.assertTrue(any(
            relation.origin is CatalogOrigin.PROJECT_PROJECTION
            for relation in detail.relations if relation.kind == "buff"
        ))

    def test_missing_character_relation_remains_explicitly_unresolved(self) -> None:
        summary = next(
            item
            for item in self.service.list_forks(page_size=200).items
            if item.exclusive_character_count == 0 and item.recommendation_count == 0
        )
        detail = self.service.get_fork(summary.fork_id)

        self.assertIsNotNone(detail)
        assert detail is not None
        self.assertTrue(any("角色归属保持未解析" in note for note in detail.audit_notes))

    def test_unavailable_buff_does_not_publish_gameplay_effect_relations(self) -> None:
        class UnavailableBuffDao(StaticCatalogForkDao):
            def list_fork_buff_links(self, fork_id: str) -> list[dict[str, Any]]:
                return [
                    {**row, "target_available": 0}
                    for row in super().list_fork_buff_links(fork_id)
                ]

        service = StaticCatalogForkService(UnavailableBuffDao(STATIC_DATABASE))
        self.addCleanup(service.close)
        summary = service.list_forks(page_size=1).items[0]
        detail = service.get_fork(summary.fork_id)

        assert detail is not None
        self.assertTrue(any(not relation.available for relation in detail.relations))
        self.assertFalse(any(
            relation.kind == "gameplay_effect" for relation in detail.relations
        ))


class StaticCatalogForkDaoTests(unittest.TestCase):
    def test_connection_rejects_writes(self) -> None:
        dao = StaticCatalogForkDao(STATIC_DATABASE)
        self.addCleanup(dao.close)

        connection = dao._connection  # The test verifies the storage boundary itself.
        self.assertIsNotNone(connection)
        assert connection is not None
        with self.assertRaises(sqlite3.OperationalError):
            connection.execute("CREATE TABLE catalog_write_probe(value INTEGER)")

    def test_character_filter_is_a_parameterized_value(self) -> None:
        dao = StaticCatalogForkDao(STATIC_DATABASE)
        self.addCleanup(dao.close)
        first = dao.list_fork_catalog_items(limit=1)

        self.assertTrue(first)
        self.assertEqual(
            dao.count_fork_catalog_items(query="%_\\"),
            0,
        )


def _run_ui_contract() -> None:
    """Exercise QWidget behavior in an isolated QApplication process."""

    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    from src.features.static_catalog.fork_detail import ForkCatalogWidget

    app = QApplication.instance() or QApplication([])
    service = StaticCatalogForkService.from_database(STATIC_DATABASE)
    widget = ForkCatalogWidget(service)
    try:
        assert widget.catalog_list.count() == 49
        assert "dataset" in widget.metadata_label.text()
        assert widget.detail_tree.topLevelItemCount() > 0

        copied_item = widget.detail_tree.topLevelItem(0).child(0)
        widget.detail_tree.setCurrentItem(copied_item)
        widget.copy_button.click()
        assert QApplication.clipboard().text().startswith("fork_")

        relations = widget.detail_tree.topLevelItem(1)
        relation_item = next(
            relations.child(index)
            for index in range(relations.childCount())
            if relations.child(index).data(1, Qt.UserRole + 1)
        )
        jumps: list[tuple[str, str]] = []
        widget.relation_jump_requested.connect(
            lambda kind, target_id: jumps.append((kind, target_id))
        )
        widget.detail_tree.itemDoubleClicked.emit(relation_item, 1)
        assert jumps[0][0] == relation_item.data(1, Qt.UserRole + 1)[0]
        unavailable = CatalogRelation(
            kind="buff",
            target_id="/Game/Missing",
            label="未解析 Buff",
            copy_value="/Game/Missing",
            origin=CatalogOrigin.PROJECT_PROJECTION,
            available=False,
        )
        unavailable_item = widget._add(
            relations, "buff", unavailable.label, unavailable.origin, relation=unavailable
        )
        assert unavailable_item.data(1, Qt.UserRole) == unavailable.copy_value
        assert unavailable_item.data(1, Qt.UserRole + 1) is None
        jump_count = len(jumps)
        widget.detail_tree.itemDoubleClicked.emit(unavailable_item, 1)
        assert len(jumps) == jump_count

        widget._page_size = 7
        widget.refresh()
        assert widget.catalog_list.count() == 7
        assert widget.next_button.isEnabled()
        widget.next_button.click()
        assert "第 2 /" in widget.page_label.text()
    finally:
        widget.deleteLater()
        service.close()
        app.processEvents()


class StaticCatalogForkUiTests(unittest.TestCase):
    def test_widget_copy_jump_lazy_detail_and_pagination_contract(self) -> None:
        environment = os.environ.copy()
        environment["QT_QPA_PLATFORM"] = "offscreen"
        completed = subprocess.run(
            [
                sys.executable,
                "-B",
                "-c",
                (
                    "from tests.test_static_catalog_fork_service "
                    "import _run_ui_contract; _run_ui_contract()"
                ),
            ],
            cwd=PROJECT_ROOT,
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
            check=False,
        )
        self.assertEqual(
            completed.returncode,
            0,
            msg=f"stdout={completed.stdout}\nstderr={completed.stderr}",
        )


if __name__ == "__main__":
    unittest.main()
