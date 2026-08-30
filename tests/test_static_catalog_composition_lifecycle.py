# 游戏资料库统一组合根的 Qt-free 生命周期与失败回滚门禁。
from __future__ import annotations

from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator
import unittest
from unittest.mock import Mock, patch

import src.features.static_catalog.dependencies as dependencies


NTE_TEST_TIER = "core"


class _Signal:
    def __init__(self) -> None:
        self._callbacks: list[object] = []

    def connect(self, callback) -> None:
        self._callbacks.append(callback)

    def emit(self, value: object) -> None:
        for callback in tuple(self._callbacks):
            callback(value)


@dataclass
class _Owned:
    close_error: Exception | None = None
    close_calls: int = 0

    def close(self) -> None:
        self.close_calls += 1
        if self.close_error is not None:
            raise self.close_error


@dataclass
class _Page:
    progression_requested: _Signal = field(default_factory=_Signal)
    dispose_calls: int = 0
    delete_later_calls: int = 0
    inventory_snapshots: list[dict[str, object]] = field(default_factory=list)
    inventory_invalidations: int = 0
    inventory_available: bool = False
    projected_results: list[dict[str, object]] = field(default_factory=list)

    def dispose(self) -> None:
        self.dispose_calls += 1

    def deleteLater(self) -> None:  # noqa: N802 - mirrors QWidget contract
        self.delete_later_calls += 1

    def apply_inventory_snapshot(self, **snapshot: object) -> None:
        self.inventory_snapshots.append(dict(snapshot))
        self.inventory_available = True

    def invalidate_inventory_projection(self) -> None:
        self.inventory_invalidations += 1
        self.inventory_available = False

    def set_progression_result(self, **result: object) -> bool:
        self.projected_results.append(dict(result))
        return True


@dataclass
class _Dialog:
    open_error: Exception | None = None
    dispose_error: Exception | None = None
    active_identity: str | None = "old-request"
    open_calls: int = 0
    dispose_calls: int = 0

    def open_request(self, request: object, *, on_result) -> None:
        del request, on_result
        self.open_calls += 1
        if self.open_error is not None:
            raise self.open_error
        self.active_identity = "new-request"

    def dispose(self) -> None:
        self.dispose_calls += 1
        self.active_identity = None
        if self.dispose_error is not None:
            raise self.dispose_error


@dataclass
class _Harness:
    dao: _Owned
    queries: _Owned
    monster_service: _Owned
    terminology: object
    character_pages: list[_Page]
    fork_pages: list[_Page]
    equipment_pages: list[_Page]
    mechanics_pages: list[_Page]
    dialogs: list[_Dialog]
    query_constructor: Mock
    release_constructor: Mock
    dialog_factory: Mock


@contextmanager
def _patched_domain_root() -> Iterator[_Harness]:
    dao = _Owned()
    queries = _Owned()
    monster_service = _Owned()
    terminology = object()
    character_pages: list[_Page] = []
    fork_pages: list[_Page] = []
    equipment_pages: list[_Page] = []
    mechanics_pages: list[_Page] = []
    dialogs: list[_Dialog] = []

    def page_factory(target: list[_Page]):
        def build(**_kwargs: object) -> _Page:
            page = _Page()
            target.append(page)
            return page

        return build

    def dialog_factory(**_kwargs: object) -> _Dialog:
        dialog = _Dialog()
        dialogs.append(dialog)
        return dialog

    with ExitStack() as stack:
        stack.enter_context(patch.object(
            dependencies,
            "StaticGameDataDao",
            return_value=dao,
        ))
        stack.enter_context(patch.object(
            dependencies,
            "StaticCatalogTerminologyService",
            return_value=terminology,
        ))
        query_constructor = stack.enter_context(patch.object(
            dependencies,
            "StaticCatalogCharacterQueries",
            return_value=queries,
        ))
        stack.enter_context(patch.object(
            dependencies,
            "StaticCatalogCharacterService",
            return_value=object(),
        ))
        release_constructor = stack.enter_context(patch.object(
            dependencies,
            "CharacterReleaseMetadataService",
            return_value=object(),
        ))
        stack.enter_context(patch.object(
            dependencies,
            "ProgressionStaminaService",
            return_value=object(),
        ))
        monster_class = stack.enter_context(patch.object(
            dependencies,
            "StaticCatalogMonsterService",
        ))
        monster_class.from_database.return_value = monster_service
        stack.enter_context(patch.object(
            dependencies,
            "build_character_catalog_page",
            side_effect=page_factory(character_pages),
        ))
        stack.enter_context(patch.object(
            dependencies,
            "build_fork_catalog_page",
            side_effect=page_factory(fork_pages),
        ))
        stack.enter_context(patch.object(
            dependencies,
            "build_equipment_catalog_page",
            side_effect=page_factory(equipment_pages),
        ))
        stack.enter_context(patch.object(
            dependencies,
            "build_monster_catalog_page",
            side_effect=lambda **_kwargs: _Page(),
        ))
        stack.enter_context(patch.object(
            dependencies,
            "build_combat_mechanics_catalog_page",
            side_effect=page_factory(mechanics_pages),
        ))
        patched_dialog_factory = stack.enter_context(patch.object(
            dependencies,
            "build_progression_calculator_dialog",
            side_effect=dialog_factory,
        ))
        yield _Harness(
            dao=dao,
            queries=queries,
            monster_service=monster_service,
            terminology=terminology,
            character_pages=character_pages,
            fork_pages=fork_pages,
            equipment_pages=equipment_pages,
            mechanics_pages=mechanics_pages,
            dialogs=dialogs,
            query_constructor=query_constructor,
            release_constructor=release_constructor,
            dialog_factory=patched_dialog_factory,
        )


def _build_specs(*, inventory_loader=None):
    return dependencies.build_static_catalog_domain_pages(
        Path("candidate") / "game_static.sqlite3",
        Path("assets") / "game_ui",
        equipment_presentation=object(),
        equipment_inventory_loader=inventory_loader,
    )


def _spec(specs, domain_key: str):
    return next(spec for spec in specs if spec.domain_key == domain_key)


class StaticCatalogCompositionLifecycleTests(unittest.TestCase):
    def test_invalid_new_progression_request_disposes_old_dialog(self) -> None:
        with _patched_domain_root() as harness:
            dialog = _Dialog(open_error=ValueError("malformed request"))
            harness.dialog_factory.side_effect = lambda **_kwargs: dialog
            specs = _build_specs()
            page = _spec(specs, "character").build(None)

            with self.assertLogs(
                "src.features.static_catalog.dependencies",
                level="ERROR",
            ):
                page.progression_requested.emit({"kind": "invalid"})

            self.assertEqual(1, dialog.open_calls)
            self.assertEqual(1, dialog.dispose_calls)
            self.assertIsNone(dialog.active_identity)

    def test_fork_dialog_construction_failure_disposes_page(self) -> None:
        with _patched_domain_root() as harness:
            harness.dialog_factory.side_effect = RuntimeError("dialog failed")
            specs = _build_specs()

            with self.assertRaisesRegex(RuntimeError, "dialog failed"):
                _spec(specs, "fork").build(None)

            self.assertEqual(1, len(harness.fork_pages))
            self.assertEqual(1, harness.fork_pages[0].dispose_calls)

    def test_initialization_failure_closes_constructed_queries_and_dao(self) -> None:
        with _patched_domain_root() as harness:
            harness.release_constructor.side_effect = RuntimeError("release failed")

            with self.assertRaisesRegex(RuntimeError, "release failed"):
                _build_specs()

            self.assertEqual(1, harness.query_constructor.call_count)
            self.assertEqual(1, harness.queries.close_calls)
            self.assertEqual(1, harness.dao.close_calls)

    def test_close_failure_does_not_skip_later_owned_callbacks(self) -> None:
        with _patched_domain_root() as harness:
            first = _Dialog(dispose_error=RuntimeError("first close failed"))
            second = _Dialog()
            harness.dialog_factory.side_effect = (first, second)
            specs = _build_specs()
            character = _spec(specs, "character")
            character.build(None)
            character.build(None)

            with self.assertRaisesRegex(RuntimeError, "关闭角色图鉴"):
                character.close()

            self.assertEqual(1, first.dispose_calls)
            self.assertEqual(1, second.dispose_calls)
            self.assertEqual(1, harness.queries.close_calls)

    def test_equipment_close_clears_refresh_targets(self) -> None:
        loader = Mock(return_value=(
            "account-a",
            7,
            {"snapshot_id": 21, "rows": ()},
        ))
        with _patched_domain_root() as harness:
            specs = _build_specs(inventory_loader=loader)
            equipment = _spec(specs, "equipment")
            page = equipment.build(None)
            self.assertEqual(1, loader.call_count)
            self.assertEqual(1, len(page.inventory_snapshots))
            self.assertEqual(1, page.inventory_invalidations)
            self.assertTrue(page.inventory_available)

            equipment.close()
            assert equipment.refresh is not None
            equipment.refresh()

            self.assertEqual(1, loader.call_count)
            self.assertEqual(1, len(page.inventory_snapshots))
            self.assertEqual([page], harness.equipment_pages)

    def test_equipment_initial_inventory_failure_rolls_back_owned_page(self) -> None:
        loader = Mock(side_effect=RuntimeError("inventory failed"))
        with _patched_domain_root() as harness:
            specs = _build_specs(inventory_loader=loader)
            equipment = _spec(specs, "equipment")

            with self.assertRaisesRegex(RuntimeError, "inventory failed"):
                equipment.build(None)

            page = harness.equipment_pages[0]
            self.assertEqual(1, page.inventory_invalidations)
            self.assertFalse(page.inventory_available)
            self.assertEqual(1, page.delete_later_calls)
            assert equipment.refresh is not None
            equipment.refresh()
            equipment.close()
            self.assertEqual(1, loader.call_count)

    def test_equipment_refresh_failure_leaves_registered_page_unavailable(self) -> None:
        loader = Mock(side_effect=(
            ("account-a", 4, {"snapshot_id": 10, "rows": ()}),
            RuntimeError("account-b failed"),
            ("account-b", 5, {"snapshot_id": 1, "rows": ()}),
        ))
        with _patched_domain_root() as harness:
            specs = _build_specs(inventory_loader=loader)
            equipment = _spec(specs, "equipment")
            page = equipment.build(None)
            self.assertTrue(page.inventory_available)

            assert equipment.refresh is not None
            with self.assertRaisesRegex(RuntimeError, "account-b failed"):
                equipment.refresh()

            self.assertEqual(2, page.inventory_invalidations)
            self.assertFalse(page.inventory_available)
            self.assertEqual(1, len(page.inventory_snapshots))

            equipment.refresh()
            self.assertTrue(page.inventory_available)
            self.assertEqual(3, page.inventory_invalidations)
            self.assertEqual("account-b", page.inventory_snapshots[-1]["account_id"])


if __name__ == "__main__":
    unittest.main()
