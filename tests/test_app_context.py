# 测试 AppContext 的数据工厂、账号切换顺序和后台服务生命周期。
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.app.context import (
    AccountContext,
    AppContext,
    ApplicationPaths,
    CallbackAccountLifecycle,
)


def account(root: Path, account_id: str) -> AccountContext:
    account_root = root / "accounts" / account_id
    return AccountContext(
        active_account_id=account_id,
        active_account_name=f"账号 {account_id}",
        account_data_root=account_root,
        user_database_path=account_root / "user_data.sqlite3",
        user_config_dir=account_root / "config",
        screenshot_dir=account_root / "scanned_images",
        log_dir=account_root / "logs",
    )


class FakeSettings:
    def __init__(self, database_path, *, legacy_config_dir=None):
        self.database_path = Path(database_path)
        self.legacy_config_dir = Path(legacy_config_dir)


class AppContextTests(unittest.TestCase):
    def make_paths(self, root: Path) -> ApplicationPaths:
        return ApplicationPaths.from_roots(
            root=root,
            app_dir=root / "app",
            data_root=root / "runtime",
            bundled_config_dir=root / "bundle" / "config",
            asset_dir=root / "bundle" / "assets",
            app_icon_path=root / "bundle" / "assets" / "app.ico",
            static_database_path=root / "bundle" / "data" / "game_static.sqlite3",
        )

    def test_injects_current_account_and_database_paths_into_narrow_factories(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = self.make_paths(root)
            self.assertEqual(
                root / "runtime" / "data" / "app_shared.sqlite3",
                paths.shared_database_path,
            )
            current = account(paths.data_root, "first")
            calls = []

            def user_factory(database_path, **identity):
                calls.append(("user", Path(database_path), identity))
                return "user-dao"

            def static_factory(database_path):
                calls.append(("static", Path(database_path)))
                return "static-dao"

            def shared_factory(database_path):
                calls.append(("shared", Path(database_path)))
                return "shared-dao"

            context = AppContext(
                paths,
                current,
                user_dao_factory=user_factory,
                static_dao_factory=static_factory,
                shared_dao_factory=shared_factory,
                settings_factory=FakeSettings,
            )

            self.assertEqual("user-dao", context.create_user_dao())
            self.assertEqual("static-dao", context.create_static_dao())
            self.assertEqual("shared-dao", context.create_shared_dao())
            self.assertEqual(
                (
                    "user",
                    current.user_database_path,
                    {
                        "account_id": "first",
                        "account_name": "账号 first",
                    },
                ),
                calls[0],
            )
            self.assertEqual(("static", paths.static_database_path), calls[1])
            self.assertEqual(("shared", paths.shared_database_path), calls[2])

    def test_switch_stops_rebuilds_notifies_and_resumes_nte_core_in_order(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = account(root, "first")
            second = account(root, "second")
            context = AppContext(
                self.make_paths(root),
                first,
                settings_factory=FakeSettings,
            )
            trace = []
            running = True

            def stop():
                nonlocal running
                trace.append(("stop", context.account.active_account_id))
                running = False

            def rebuild(current):
                trace.append(
                    (
                        "rebuild",
                        current.active_account_id,
                        context.account.active_account_id,
                    )
                )

            def start():
                nonlocal running
                trace.append(("start", context.account.active_account_id))
                running = True

            lifecycle = CallbackAccountLifecycle(
                is_running=lambda: running,
                stop=stop,
                rebuild=rebuild,
                start=start,
            )
            context.register_account_lifecycle(lifecycle, nte_core=True)
            context.subscribe_account_changed(
                lambda event: trace.append(
                    (
                        "notify",
                        event.previous.active_account_id,
                        event.current.active_account_id,
                        event.generation,
                    )
                )
            )

            event = context.switch_account(second)

            self.assertIs(context.nte_core_lifecycle, lifecycle)
            self.assertEqual(1, context.generation)
            self.assertEqual(second, context.account)
            self.assertEqual(second.user_database_path, context.account_settings.database_path)
            self.assertEqual(
                [
                    ("stop", "first"),
                    ("rebuild", "second", "second"),
                    ("notify", "first", "second", 1),
                    ("start", "second"),
                ],
                trace,
            )
            self.assertEqual(second, event.current)
            self.assertTrue(running)

    def test_stopped_service_stays_stopped_and_same_account_is_no_op(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = account(root, "first")
            second = account(root, "second")
            context = AppContext(
                self.make_paths(root),
                first,
                settings_factory=FakeSettings,
            )
            trace = []
            lifecycle = CallbackAccountLifecycle(
                is_running=lambda: False,
                stop=lambda: trace.append("stop"),
                rebuild=lambda current: trace.append(
                    f"rebuild:{current.active_account_id}"
                ),
                start=lambda: trace.append("start"),
            )
            context.register_account_lifecycle(lifecycle)

            self.assertIsNone(context.switch_account(first))
            self.assertEqual([], trace)
            context.switch_account(second)
            self.assertEqual(["stop", "rebuild:second"], trace)

    def test_stop_failure_still_switches_account_and_resumes_services(self):
        """停止超时不得让切换半途失败：账号、代次与服务恢复都必须完成。"""

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = account(root, "first")
            second = account(root, "second")
            context = AppContext(
                self.make_paths(root),
                first,
                settings_factory=FakeSettings,
            )
            trace = []

            def stop():
                trace.append("stop")
                raise TimeoutError("采集线程未在超时内结束")

            context.register_account_lifecycle(
                CallbackAccountLifecycle(
                    is_running=lambda: True,
                    stop=stop,
                    rebuild=lambda current: trace.append(
                        f"rebuild:{current.active_account_id}"
                    ),
                    start=lambda: trace.append("start"),
                )
            )

            context.switch_account(second)

            self.assertEqual(second, context.account)
            self.assertEqual(1, context.generation)
            self.assertEqual(["stop", "rebuild:second", "start"], trace)

    def test_handler_failure_does_not_skip_other_handlers_or_service_restart(self):
        """单个通知回调抛错不得跳过其余回调与账号服务恢复。"""

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = account(root, "first")
            second = account(root, "second")
            context = AppContext(
                self.make_paths(root),
                first,
                settings_factory=FakeSettings,
            )
            trace = []

            def failing_handler(_event):
                trace.append("handler-1")
                raise RuntimeError("回调内部错误")

            context.subscribe_account_changed(failing_handler)
            context.subscribe_account_changed(lambda _event: trace.append("handler-2"))
            context.register_account_lifecycle(
                CallbackAccountLifecycle(
                    is_running=lambda: True,
                    stop=lambda: trace.append("stop"),
                    rebuild=lambda _current: None,
                    start=lambda: trace.append("start"),
                )
            )

            context.switch_account(second)

            self.assertEqual(second, context.account)
            self.assertEqual(
                ["stop", "handler-1", "handler-2", "start"], trace,
            )

    def test_rebuild_failure_does_not_skip_service_restart(self):
        """单个 lifecycle 重建失败不得阻止服务恢复。"""

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = account(root, "first")
            second = account(root, "second")
            context = AppContext(
                self.make_paths(root),
                first,
                settings_factory=FakeSettings,
            )
            trace = []
            context.register_account_lifecycle(
                CallbackAccountLifecycle(
                    is_running=lambda: True,
                    stop=lambda: trace.append("stop"),
                    rebuild=lambda _current: (_ for _ in ()).throw(
                        RuntimeError("重建失败")
                    ),
                    start=lambda: trace.append("start"),
                )
            )

            context.switch_account(second)

            self.assertEqual(second, context.account)
            self.assertEqual(["stop", "start"], trace)


if __name__ == "__main__":
    unittest.main()
