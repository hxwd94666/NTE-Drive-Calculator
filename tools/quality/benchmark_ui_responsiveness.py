# 在隔离数据副本上比较真实角色页面回调、就绪时间和事件循环延迟。
"""Reproducible offscreen UI benchmark; never resolves a live account implicitly."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import statistics
import sys
import time
from types import SimpleNamespace


def summarize(values: list[float]) -> dict[str, float]:
    ordered = sorted(values)
    return {
        "median_ms": round(statistics.median(ordered), 3),
        "p95_ms": round(ordered[math.ceil(len(ordered) * 0.95) - 1], 3),
        "max_ms": round(max(ordered), 3),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--iterations", type=int, default=30)
    parser.add_argument("--models-only", action="store_true", help="Hash all role display models, excluding deferred candidates")
    args = parser.parse_args()
    if args.iterations < 1:
        parser.error("iterations must be positive")
    fixture = args.fixture.resolve()
    marker = fixture / "ui-benchmark-fixture.json"
    if not marker.is_file() or not json.loads(marker.read_text(encoding="utf-8")).get("isolated"):
        parser.error("requires an explicitly prepared isolated fixture")
    for name in ("user.sqlite3", "game_static.sqlite3", "shared.sqlite3"):
        resolved = (fixture / name).resolve()
        if resolved.parent != fixture or not resolved.is_file():
            parser.error("fixture databases must be physical files inside the isolated directory")
    source = args.source_root.resolve()
    sys.path.insert(0, str(source))
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    os.environ["NTE_GAME_STATIC_DB"] = str(fixture / "game_static.sqlite3")
    os.environ["NTE_WORKSHOP_WEIGHT_TEMPLATE_FILE"] = str(fixture / "config/workshop_weight_template.json")
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget
    from src.features.official_role import role_shell
    from src.utils.logger import logger

    # 两版使用相同日志传输设置，避免控制台耗时污染同条件比较。
    logger.remove()
    app = QApplication.instance() or QApplication([])
    assets = json.loads(marker.read_text(encoding="utf-8")).get("asset_root")
    window = QWidget()
    window.app_context = SimpleNamespace(
        account=SimpleNamespace(active_account_id="ui-benchmark", user_database_path=fixture / "user.sqlite3"),
        generation=1,
        paths=SimpleNamespace(
            static_database_path=fixture / "game_static.sqlite3",
            shared_database_path=fixture / "shared.sqlite3",
            role_catalog=SimpleNamespace(dataset_id="benchmark", sha256="fixed", asset_root=Path(assets)) if assets else None,
        ),
    )
    if args.models_only:
        import inspect
        from src.features.official_role.controller import OfficialRoleController
        from src.features.official_role.dependencies import OfficialRoleDependencies

        controller = OfficialRoleController(OfficialRoleDependencies.from_app_context(window.app_context))

        def normalized(value):
            if isinstance(value, dict):
                return {key: normalized(item) for key, item in value.items()
                        if key not in {"replacement_items", "replacement_candidates_loaded"}}
            if isinstance(value, (list, tuple)):
                return [normalized(item) for item in value]
            return value

        supports_light = "include_replacement_candidates" in inspect.signature(controller.load_detail).parameters
        hashes = []
        for row in controller.load_index():
            kwargs = {"include_replacement_candidates": False} if supports_light else {}
            model = normalized(controller.load_detail(int(row["character_id"]), **kwargs))
            hashes.append(hashlib.sha256(json.dumps(model, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest())
        result = {"scope": "all role display fields except explicitly deferred candidate pools", "model_count": len(hashes),
                  "model_digests": hashes}
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(result))
        return 0
    window._go = lambda _key: None
    outer = QVBoxLayout(window)
    window.resize(1280, 900)
    ticks: list[float] = []
    last_tick = time.perf_counter()

    def tick() -> None:
        nonlocal last_tick
        now = time.perf_counter()
        ticks.append(max(0.0, (now - last_tick) * 1000 - 10))
        last_tick = now

    timer = QTimer(window)
    timer.setInterval(10)
    timer.timeout.connect(tick)
    timer.start()
    rows: list[dict[str, object]] = []

    def ready() -> bool:
        tabs = getattr(window, "official_role_tabs", None)
        scroll = tabs.currentWidget() if tabs is not None else None
        owner = getattr(window, "_official_role_controller", None)
        reading = bool(getattr(owner, "is_loading", lambda: False)())
        return bool(scroll is not None and scroll.property("loaded") and not reading)

    def drain_until_ready(started: float) -> None:
        while not ready():
            app.processEvents()
            if time.perf_counter() - started > 60:
                raise TimeoutError("role page did not become ready")
            time.sleep(0.001)
        # Include pending layout/paint delivery in measured UI heartbeat.
        until = time.perf_counter() + 0.035
        while time.perf_counter() < until:
            app.processEvents()
            time.sleep(0.001)

    for index in range(args.iterations + 1):
        ticks.clear()
        last_tick = time.perf_counter()
        started = time.perf_counter()
        if index == 0:
            page = role_shell._page_my_role(window)
            outer.addWidget(page)
            window.show()
        else:
            role_shell._refresh_my_role(window)
        callback_ms = (time.perf_counter() - started) * 1000
        drain_until_ready(started)
        rows.append({
            "case": "first_enter" if index == 0 else "repeat_enter",
            "callback_ms": round(callback_ms, 3),
            "ready_ms": round((time.perf_counter() - started) * 1000 - 35, 3),
            "max_event_loop_delay_ms": round(max(ticks, default=0), 3),
            "widget_count": len(window.findChildren(QWidget)),
        })

    tabs = window.official_role_tabs
    current_id = int(tabs.tabBar().tabData(tabs.currentIndex()))
    detail = window._official_role_editors[current_id]["detail"]
    # 只输出稳定计算字段的指纹，不附账号身份或装备正文。
    stable = {key: detail.get(key) for key in (
        "profile", "base_stats", "property_weights", "main_property_weights", "catalog_scope",
    )}
    digest = hashlib.sha256(json.dumps(stable, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()
    warm = rows[1:]
    result = {
        "schema": "calc.ui-responsiveness-benchmark/1",
        "environment": {"python": sys.version.split()[0], "platform": sys.platform, "qt_platform": "offscreen"},
        "scope": "real role page and real isolated SQLite input; not game, full-app navigation or GPU timing",
        "iterations": args.iterations,
        "role_count": tabs.count(),
        "business_digest": digest,
        "first_enter": rows[0],
        "repeat_enter": {key: summarize([float(row[key]) for row in warm]) for key in (
            "callback_ms", "ready_ms", "max_event_loop_delay_ms",
        )},
        "samples": rows,
    }
    owner = getattr(window, "_official_role_controller", None)
    if hasattr(owner, "close"):
        owner.close()
    window.close()
    timer.stop()
    app.processEvents()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: value for key, value in result.items() if key != "samples"}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
