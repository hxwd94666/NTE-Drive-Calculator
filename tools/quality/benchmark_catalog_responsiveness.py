# 在隔离数据库上比较计算目录刷新、无关写入及新权重就绪的前后表现。
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import statistics
import sys
import tempfile
import time
from types import SimpleNamespace


def summary(values):
    ordered = sorted(values)
    return {"median_ms": round(statistics.median(ordered), 3),
            "p95_ms": round(ordered[math.ceil(len(ordered) * .95) - 1], 3), "max_ms": round(ordered[-1], 3)}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--iterations", type=int, default=30)
    args = parser.parse_args()
    fixture = args.fixture.resolve()
    marker = fixture / "ui-benchmark-fixture.json"
    if args.iterations < 1 or not marker.is_file() or not json.loads(marker.read_text()).get("isolated"):
        parser.error("requires a positive iteration count and explicitly isolated fixture")
    for name in ("user.sqlite3", "game_static.sqlite3"):
        path = (fixture / name).resolve()
        if path.parent != fixture or not path.is_file():
            parser.error("databases must be physical files inside the isolated fixture")
    sys.path.insert(0, str(args.source_root.resolve()))
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    os.environ["NTE_GAME_STATIC_DB"] = str(fixture / "game_static.sqlite3")
    os.environ["NTE_WORKSHOP_WEIGHT_TEMPLATE_FILE"] = str(fixture / "config/workshop_weight_template.json")
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication, QLabel, QPushButton, QVBoxLayout, QWidget
    from src.features.allocation.role_selector import RoleSelector
    from src.services.characters.character_weight_service import save_account_character_weights
    from src.storage.sqlite.user_data_dao import UserDataDao
    from src.ui.main_window_data_mixin import MainWindowDataMixin
    from src.utils.logger import logger
    logger.remove()
    app = QApplication.instance() or QApplication([])
    with tempfile.TemporaryDirectory(prefix="catalog-benchmark-", dir=fixture) as temporary:
        working = Path(temporary) / "user.sqlite3"
        assert fixture in working.resolve().parents
        original = sqlite3.connect((fixture / "user.sqlite3").as_uri() + "?mode=ro", uri=True)
        clone = sqlite3.connect(working)
        try:
            original.backup(clone)
        finally:
            original.close()
            clone.close()

        class Window(QWidget, MainWindowDataMixin):
            def __init__(self):
                super().__init__()
                paths = SimpleNamespace(config_dir=fixture / "config", workshop_weight_template_file=fixture / "config/workshop_weight_template.json",
                    equipment_allocation_database_path=fixture / "game_static.sqlite3", equipment_allocation_asset_root=Path(json.loads(marker.read_text())["asset_root"]))
                self.app_context = SimpleNamespace(paths=paths, generation=1,
                    account=SimpleNamespace(active_account_id="isolated", user_database_path=working))
                self.btn_run, self.status_lbl = QPushButton(self), QLabel(self)
                self.selector = RoleSelector(self, priority_config_path_provider=lambda: working.parent / "priority.json")
                QVBoxLayout(self).addWidget(self.selector)
                self.resize(1200, 800)
                self.scanning_controller = SimpleNamespace(is_running=lambda: False, role_selector=self.selector)
                self.card_rebuilds = self.catalog_reads = 0
                self.load_roles = self.selector.load_roles
                self.selector.load_roles = self.cards
                self.equipment_presentation = SimpleNamespace(update_catalog=lambda **_kwargs: None)
                self.identification_controller = SimpleNamespace(update_catalog=lambda **_kwargs: None)

            def cards(self, *_args, **_kwargs):
                self.card_rebuilds += 1
                self.load_roles(*_args, **_kwargs)

            def _read_allocation_catalog(self, *parameters):
                self.catalog_reads += 1
                return MainWindowDataMixin._read_allocation_catalog(*parameters)

            def ready(self):
                controller = getattr(self, "allocation_catalog_controller", None)
                if controller is not None:
                    return not controller.lane.is_running() and self.btn_run.isEnabled()
                worker = getattr(self, "_allocation_catalog_worker", None)
                try:
                    finished = worker is None or not worker.isRunning()
                except RuntimeError:
                    finished = True  # The baseline disposes finished workers with deleteLater.
                return finished and getattr(self, "_allocation_catalog_pending_key", None) is None

            def model(self):
                return digest((self.stats_config, self.roles_db, self.sets_db, self._shape_areas, self.weapons_db, self._allocation_icon_paths))

        window = Window()
        window.show()
        delays, last_tick = [], time.perf_counter()
        def tick():
            nonlocal last_tick
            now = time.perf_counter()
            delays.append(max(0, (now - last_tick) * 1000 - 10))
            last_tick = now
        timer = QTimer(window)
        timer.timeout.connect(tick)
        timer.start(10)

        def measure(action):
            nonlocal last_tick
            app.processEvents()
            delays.clear()
            last_tick = start = time.perf_counter()
            action()
            returned = time.perf_counter()
            deadline = start + 60
            while not window.ready() and time.perf_counter() < deadline:
                app.processEvents()
                time.sleep(.001)
            app.processEvents()
            if not window.ready() or not window.btn_run.isEnabled():
                raise RuntimeError("catalog did not become ready")
            return {"callback_ms": (returned-start)*1000, "ready_ms": (time.perf_counter()-start)*1000,
                    "event_loop_max_ms": max(delays, default=0)}

        try:
            cold = measure(window._load_data)
            frozen = window.scoring_engine
            frozen_digest = digest(frozen.roles_db)
            models, results = [], {}
            initial_model = window.model()
            for stage in ("unchanged", "unrelated_write", "weight_edit"):
                samples = []
                before_reads, before_cards = window.catalog_reads, window.card_rebuilds
                count = args.iterations if stage != "weight_edit" else 5
                for index in range(count):
                    if stage == "unrelated_write":
                        with UserDataDao(working) as dao:
                            dao.replace_application_setting_copy("benchmark_unrelated", {"counter": index})
                    if stage == "weight_edit":
                        save_account_character_weights(working, 1051, {"CritBase": float(index+2)}, static_database_path=fixture / "game_static.sqlite3")
                    samples.append(measure(window._refresh_execute))
                    if stage == "weight_edit":
                        matching = [row for row in window.roles_db.values() if row.get("character_id") == 1051]
                        if len(matching) != 1 or matching[0]["weights"].get("暴击率%") != float(index+2):
                            raise AssertionError("latest weights were not applied")
                        models.append(window.model())
                    elif window.model() != initial_model:
                        raise AssertionError("unrelated writes changed the catalog")
                results[stage] = {"samples": samples, "catalog_reads": window.catalog_reads-before_reads,
                    "card_rebuilds": window.card_rebuilds-before_cards,
                    **{field: summary([row[field] for row in samples]) for field in samples[0]}}
            if digest(frozen.roles_db) != frozen_digest:
                raise AssertionError("a previous frozen engine was mutated")
            output = {"iterations": args.iterations, "cold": cold, "results": results,
                      "initial_model_digest": initial_model, "weight_model_digests": models, "frozen_engine_unchanged": True}
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(output, indent=2), encoding="utf-8")
            print(json.dumps({key: value for key, value in output.items() if key != "results"}))
        finally:
            controller = getattr(window, "allocation_catalog_controller", None)
            if controller is not None:
                controller.close()
                while controller.lane.is_running():
                    app.processEvents()
                    time.sleep(.001)
            timer.stop()
            window.close()
            window.deleteLater()
            app.processEvents()


if __name__ == "__main__":
    main()
