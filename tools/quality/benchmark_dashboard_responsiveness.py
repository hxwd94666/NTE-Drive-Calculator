# 用合成数据库的一致性副本测量真实工作台回调、事件循环与版本合并，并有界收尾 owner。
"""工作台专用代理基准；不启动完整窗口、同步、游戏、设备或驱动。"""

from __future__ import annotations

import argparse
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import time
from types import SimpleNamespace


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _inputs(fixture: Path) -> dict[str, str]:
    # 只接受既有合成夹具；不把实际账号副本补一个标记后当作公共样本。
    marker = fixture / "ui-benchmark-fixture.json"
    content = json.loads(marker.read_text(encoding="utf-8"))
    if content.get("isolated") is not True or content.get("synthetic") is not True:
        raise ValueError("需要显式 isolated/synthetic 的合成夹具")
    paths = {name: fixture / name for name in (
        "user.sqlite3", "game_static.sqlite3", "ui-benchmark-fixture.json",
    )}
    for path in paths.values():
        if not path.is_file() or path.is_symlink() or path.resolve().parent != fixture:
            raise ValueError("夹具成员必须是目录内的真实文件")
    return {name: _sha(path) for name, path in paths.items()}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--iterations", type=int, default=30)
    args = parser.parse_args()
    fixture, source, output_path = (
        args.fixture.resolve(), args.source_root.resolve(), args.output.resolve(),
    )
    if args.iterations < 1:
        parser.error("iterations 必须为正数")
    try:
        inputs = _inputs(fixture)
    except (OSError, ValueError) as error:
        parser.error(str(error))
    if output_path.exists() or output_path.is_relative_to(fixture):
        parser.error("输出须为夹具之外的全新文件，不覆盖既有记录")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    sys.path.insert(0, str(source))
    from tools.quality.benchmark_weight_responsiveness import (
        import_audit, isolate_source, summarize,
    )
    isolate_source(source)
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    os.environ["NTE_GAME_STATIC_DB"] = str(fixture / "game_static.sqlite3")
    from PySide6.QtCore import QEvent, QTimer
    from PySide6.QtWidgets import QApplication, QLabel, QWidget
    from shiboken6 import isValid
    from src.features.home.page import refresh_home_page
    from src.services.accounts.dashboard import DashboardService
    from src.ui.controllers.dashboard_controller import DashboardController, DashboardDependencies
    from src.utils.logger import logger

    logger.remove()
    app = QApplication.instance() or QApplication([])
    models, failures, reads, delays, rows = [], [], [], [], []
    source_paths = (
        "src/services/accounts/dashboard.py", "src/ui/controllers/dashboard_controller.py",
        "src/app/page_tasks.py", "src/features/home/page.py",
        "tools/quality/benchmark_weight_responsiveness.py",
    )
    source_hashes = {name: _sha(source / name) for name in source_paths}
    # SQLite 活库只经一致性备份读取；原夹具不承担迁移或业务读取造成的写入。
    temporary = tempfile.TemporaryDirectory(prefix="dashboard-measure-", dir=output_path.parent)
    owned = Path(temporary.name).resolve()
    if not owned.is_relative_to(output_path.parent) or owned.is_symlink():
        raise ValueError("隔离目录归属变化")
    window = controller = timer = None
    original_load = DashboardService.load
    result = {"temporary_directory": str(owned), "schema": "calc.dashboard-responsiveness/2", "iterations": args.iterations,
              "scope": "当前真实工作台及合成 SQLite 副本；非完整应用/真机/游戏/驱动验收"}
    status = 0
    started = None
    try:
        database = owned / "user.sqlite3"
        with closing(sqlite3.connect((fixture / "user.sqlite3").as_uri() + "?mode=ro", uri=True)) as original:
            with closing(sqlite3.connect(database)) as clone:
                original.backup(clone)
        window = QWidget()
        for name in ("home_account_label", "home_character_sync_detail", "home_last_sync_label"):
            setattr(window, name, QLabel(window))
        window.home_metric_labels = {key: (QLabel(window), QLabel(window)) for key in (
            "inventory", "module", "core", "equipped", "plans", "characters",
        )}
        window.auto_sync_controller = SimpleNamespace(render=lambda: None)

        def measured_load(service):
            started = time.perf_counter()
            try:
                return original_load(service)
            finally:
                reads.append((time.perf_counter() - started) * 1000)

        DashboardService.load = measured_load

        def apply(model):
            refresh_home_page(window, model)
            models.append(model)

        controller = DashboardController(
            dependencies=lambda: DashboardDependencies(1, database, fixture / "game_static.sqlite3"),
            apply=apply, failed=failures.append, loading=lambda: None, parent=window,
        )

        def pump(predicate, timeout=30, *, check_errors=True):
            deadline = time.perf_counter() + timeout
            while not predicate():
                app.processEvents()
                if check_errors and failures:
                    raise RuntimeError(failures[-1])
                if time.perf_counter() >= deadline:
                    raise TimeoutError("工作台任务未在预算内到达物理终态")
                time.sleep(.001)

        last = time.perf_counter()

        def tick():
            nonlocal last
            now = time.perf_counter()
            delays.append(max(0, (now - last) * 1000 - 10))
            last = now

        timer = QTimer(window)
        timer.timeout.connect(tick)
        timer.start(10)
        for index in range(args.iterations + 1):
            # 真实 retry 绕过去重；就绪包含读取、投影及原 worker 物理退出。
            pump(lambda: bool(delays))
            delays.clear()
            last = started = time.perf_counter()
            count = len(models)
            if index == 0:
                controller.set_visible(True)
            else:
                controller.retry()
            callback = (time.perf_counter() - started) * 1000
            pump(lambda: len(models) > count and not controller._reads.is_running()
                 and not controller._timer.isActive())
            ready = (time.perf_counter() - started) * 1000
            pump(lambda: bool(delays))
            rows.append({"callback_ms": callback, "ready_ms": ready,
                         "max_event_loop_delay_ms": max(delays, default=0)})

        controller.refresh(version=("run", 1, 1))
        pump(lambda: not controller._reads.is_running() and not controller._timer.isActive())
        read_count = len(reads)
        for _ in range(args.iterations):
            controller.refresh(version=("run", 1, 1))
        end = time.perf_counter() + .55
        pump(lambda: time.perf_counter() >= end)
        same_version_reads = len(reads) - read_count
        controller.set_visible(False)
        read_count = len(reads)
        for index in range(args.iterations):
            controller.refresh(version=("run", index + 2, 1))
        end = time.perf_counter() + .55
        pump(lambda: time.perf_counter() >= end)
        hidden_reads = len(reads) - read_count
        count = len(models)
        controller.set_visible(True)
        pump(lambda: len(models) > count and not controller._reads.is_running())
        digests = {hashlib.sha256(json.dumps(model, sort_keys=True, default=str).encode()).hexdigest()
                   for model in models}
        if len(digests) != 1 or same_version_reads != 0 or hidden_reads != 0:
            raise AssertionError("固定模型/重复版本/隐藏页读取语义发生变化")
        result.update(
            status="passed", business_digest=next(iter(digests)),
            inventory_count=models[-1]["inventory"]["stored_item_count"] if models[-1]["inventory"] else 0,
            first_enter=rows[0],
            repeat_enter={key: summarize([row[key] for row in rows[1:]]) for key in rows[0]},
            repeated_same_version_reads=same_version_reads, hidden_notification_reads=hidden_reads,
            reads_ms=summarize(reads), samples=rows,
            environment={"python": sys.version.split()[0], "platform": sys.platform, "qt_platform": "offscreen"},
        )
    except Exception as error:
        status = 1
        result.update(status="failed", error=f"{type(error).__name__}: {error}",
                      failure_observation={"reads_ms": reads,
                          "max_event_loop_delay_ms": max(delays, default=0),
                          "action_ms": (time.perf_counter() - started) * 1000 if started is not None else None})
    finally:
        # 各收尾阶段独立记错：保留原业务异常，也保留后验失败的真实未知状态。
        def cleanup_failed(stage, error):
            nonlocal status
            status = 1
            result["status"] = "failed"
            result.setdefault("cleanup_errors", []).append(
                {"stage": stage, "error": f"{type(error).__name__}: {error}"})

        settled = controller is None
        if controller is not None:
            try:
                controller.close()
            except Exception as error:
                cleanup_failed("cancel_delivery", error)
            try:
                deadline = time.perf_counter() + 30
                while controller._reads.is_running() and time.perf_counter() < deadline:
                    app.processEvents()
                    time.sleep(.001)
                settled = not controller._reads.is_running()
            except Exception as error:
                cleanup_failed("physical_tasks", error)
        DashboardService.load = original_load
        if timer is not None:
            try:
                timer.stop()
            except Exception as error:
                cleanup_failed("heartbeat_stop", error)

        destroyed = window is None
        if settled and window is not None:
            try:
                window.close()
                window.deleteLater()
                app.sendPostedEvents(None, QEvent.DeferredDelete)
                destroyed = not isValid(window)
            except Exception as error:
                cleanup_failed("owner_destroy", error)
        result["owner_destroyed"] = destroyed if settled else None
        if not settled or not destroyed:
            status = 1
            result.update(status="failed", cleanup="物理任务或owner终态未确认，隔离目录保留")
            temporary._finalizer.detach()
        else:
            try:
                if not owned.is_relative_to(output_path.parent) or owned.is_symlink():
                    raise ValueError("清理前隔离目录归属变化")
                temporary.cleanup()
                result["cleanup"] = "物理任务结束且owner销毁后清理独立副本"
            except Exception as error:
                cleanup_failed("clone_cleanup", error)
                temporary._finalizer.detach()
                result["cleanup"] = "独立副本清理失败，保留目录"

        try:
            result["inputs_hashes_unchanged"] = _inputs(fixture) == inputs
        except Exception as error:
            result["inputs_hashes_unchanged"] = None
            cleanup_failed("fixture_postcheck", error)
        try:
            result["source_hashes_unchanged"] = {
                name: _sha(source / name) for name in source_paths} == source_hashes
        except Exception as error:
            result["source_hashes_unchanged"] = None
            cleanup_failed("source_postcheck", error)
        try:
            result["imports"] = import_audit(source)
        except Exception as error:
            result["imports"] = None
            cleanup_failed("import_postcheck", error)
        if (result["inputs_hashes_unchanged"] is not True
                or result["source_hashes_unchanged"] is not True
                or result["imports"] is None or result["imports"]["foreign_project_modules"]):
            status = 1
            result["status"] = "failed"
        # 最后独立尝试独占写终态；输出自身写失败如实报错，不冒称存在失败文件。
        try:
            with output_path.open("x", encoding="utf-8") as output:
                json.dump(result, output, ensure_ascii=False, indent=2)
        except Exception as error:
            cleanup_failed("record_write", error)
            print(json.dumps({"record_write_failed": True, "output": str(output_path),
                              "error": str(error)}, ensure_ascii=False), file=sys.stderr)
    print(json.dumps({key: value for key, value in result.items() if key not in ("samples", "imports")},
                     ensure_ascii=False))
    return status


if __name__ == "__main__":
    raise SystemExit(main())
