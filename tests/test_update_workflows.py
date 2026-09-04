# 覆盖更新检查、Mirror 响应与安装包下载行为。

import tempfile
import unittest
import urllib.error
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch



class UpdateWorkflowTests(unittest.TestCase):
    def test_public_project_links_and_group_notice(self):
        from src.app.constants import (
            BILIBILI_HOME_URL,
            GITHUB_HOME_URL,
            GITHUB_LATEST_RELEASE_URL,
            GROUP_CHAT_NOTICE,
            MIRROR_PROJECT_URL,
            SUPPORT_US_URL,
        )
        from src.ui.controllers import update_controller

        opened = []
        window = SimpleNamespace(_open_url=opened.append)
        update_controller._open_update_homepage(window)
        update_controller._open_bilibili_homepage(window)
        update_controller._open_project_homepage(window)
        update_controller._open_support_homepage(window)

        self.assertEqual(
            [
                GITHUB_LATEST_RELEASE_URL,
                BILIBILI_HOME_URL,
                GITHUB_HOME_URL,
                SUPPORT_US_URL,
            ],
            opened,
        )
        self.assertEqual(
            "https://github.com/hxwd94666/NTE-Drive-Calculator/releases/latest",
            GITHUB_LATEST_RELEASE_URL,
        )
        self.assertEqual("https://afdian.com/a/hxwd94666", SUPPORT_US_URL)
        self.assertEqual(
            "https://mirrorchyan.com/zh/projects?rid=NTE-Drive-Calc&channel=stable",
            MIRROR_PROJECT_URL,
        )
        self.assertEqual(
            "QQ交流群：1029030672\n开发交流群请入群私聊群主。",
            GROUP_CHAT_NOTICE,
        )
        with patch.object(update_controller.QMessageBox, "information") as information:
            update_controller._show_group_chat_notice(window)
        information.assert_called_once_with(window, "加入群聊", GROUP_CHAT_NOTICE)

    def test_mirror_download_failure_link_opens_the_project_page(self):
        from src.app.constants import MIRROR_PROJECT_URL
        from src.ui.controllers import update_controller

        link_text = update_controller._mirror_project_link_text("尝试下载")

        self.assertIn("Mirror 项目页面尝试下载", link_text)
        self.assertIn(f'href="{MIRROR_PROJECT_URL}"', link_text)
        self.assertIn(MIRROR_PROJECT_URL, link_text)

    def test_mirror_download_only_allows_current_or_newer_release(self):
        from src.ui.controllers import update_controller

        self.assertTrue(
            update_controller._mirror_download_version_is_available("v2.1.0", "v2.1.0")
        )
        self.assertTrue(
            update_controller._mirror_download_version_is_available("v2.1.1", "v2.1.0")
        )
        self.assertFalse(
            update_controller._mirror_download_version_is_available("v2.0.9", "v2.1.0")
        )

    def test_update_check_default_timeout_is_short(self):
        from src.features.settings import updates

        seen_timeouts = []
        original_urlopen = updates.urllib.request.urlopen

        def fake_urlopen(_request, **kwargs):
            seen_timeouts.append(kwargs.get("timeout"))
            raise urllib.error.URLError("network unavailable")

        updates.urllib.request.urlopen = fake_urlopen
        try:
            updates.fetch_update_info(
                "https://example.invalid/latest",
                "1.1.0",
            )
        finally:
            updates.urllib.request.urlopen = original_urlopen

        self.assertTrue(seen_timeouts)
        self.assertTrue(all(timeout <= 5 for timeout in seen_timeouts))

    def test_startup_update_error_updates_status_without_prompt(self):
        import src.ui.app as app_module

        class Label:
            def __init__(self):
                self.text = ""

            def setText(self, text):
                self.text = text

        class Window:
            def __init__(self):
                self._update_check_manual = False
                self._update_status = Label()
                self.prompts = []

            def _show_update_failure_netdisk_prompt(self, detail=""):
                self.prompts.append(detail)

        window = Window()
        app_module.MainWindow._on_update_error(window, "timeout")

        self.assertIn("Mirror 酱", window._update_status.text)
        self.assertEqual([], window.prompts)

    def test_update_network_failure_returns_user_facing_result(self):
        from src.features.settings import updates

        original_urlopen = updates.urllib.request.urlopen
        updates.urllib.request.urlopen = lambda *_args, **_kwargs: (_ for _ in ()).throw(
            urllib.error.URLError(OSError(10061, "connection refused"))
        )
        try:
            info = updates.fetch_update_info(
                "https://example.invalid/latest",
                "1.1.0",
                timeout=1,
            )
        finally:
            updates.urllib.request.urlopen = original_urlopen

        self.assertFalse(info["has_release"])
        self.assertFalse(info["newer"])
        self.assertEqual("", info["url"])
        self.assertEqual("Mirror 酱更新服务请求失败，请稍后重试。", info["message"])
        self.assertNotIn("Traceback", info["message"])

    def test_mirror_error_code_returns_user_facing_result(self):
        from src.features.settings import updates

        original_urlopen = updates.urllib.request.urlopen

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return '{"code":4001,"msg":"CDK 无效"}'.encode("utf-8")

        updates.urllib.request.urlopen = lambda *_args, **_kwargs: Response()
        try:
            info = updates.fetch_update_info(
                "https://example.invalid/latest",
                "1.1.0",
                timeout=1,
            )
        finally:
            updates.urllib.request.urlopen = original_urlopen

        self.assertFalse(info["has_release"])
        self.assertTrue(info["error"])
        self.assertEqual("CDK 无效", info["message"])

    def test_mirror_update_response_includes_version_notes_and_download_url(self):
        from src.features.settings import updates

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return (
                    '{"code":0,"msg":"success","data":{'
                    '"version_name":"v2.1.0","url":"https://download.example/file",'
                    '"release_note":"修复配装页面"}}'
                ).encode("utf-8")

        original_urlopen = updates.urllib.request.urlopen
        requested_urls = []

        def fake_urlopen(request, **_kwargs):
            url = request.full_url if hasattr(request, "full_url") else str(request)
            requested_urls.append(url)
            return Response()

        updates.urllib.request.urlopen = fake_urlopen
        try:
            info = updates.fetch_update_info(
                "https://mirrorchyan.com/api/resources/NTE-Drive-Calc/latest",
                "2.0.0",
                cdk="0001bf xxxxxx",
                timeout=1,
            )
        finally:
            updates.urllib.request.urlopen = original_urlopen

        self.assertTrue(info["has_release"])
        self.assertTrue(info["newer"])
        self.assertEqual("v2.1.0", info["latest"])
        self.assertEqual("https://download.example/file", info["url"])
        self.assertEqual("修复配装页面", info["message"])
        self.assertEqual(
            "https://mirrorchyan.com/api/resources/NTE-Drive-Calc/latest?current_version=2.0.0&cdk=0001bf+xxxxxx",
            requested_urls[0],
        )

    def test_mirror_installer_download_writes_valid_exe_and_reports_progress(self):
        from src.features.settings import updates

        class Response:
            headers = {"Content-Length": "8"}

            def __init__(self):
                self._blocks = [b"MZtest", b"ok", b""]

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self, _size):
                return self._blocks.pop(0)

        original_urlopen = updates.urllib.request.urlopen
        updates.urllib.request.urlopen = lambda *_args, **_kwargs: Response()
        updates_progress = []
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                result = updates.download_update_installer(
                    "https://download.example/NTE-Drive-Calc.exe",
                    destination_dir=temp_dir,
                    progress_callback=lambda current, total: updates_progress.append((current, total)),
                )
                installer = Path(result["path"])
                self.assertEqual(b"MZtestok", installer.read_bytes())
                self.assertEqual(8, result["downloaded"])
        finally:
            updates.urllib.request.urlopen = original_urlopen

        self.assertEqual((0, 8), updates_progress[0])
        self.assertEqual((8, 8), updates_progress[-1])

    def test_mirror_installer_download_removes_partial_file_when_cancelled(self):
        from src.features.settings import updates

        class Response:
            headers = {"Content-Length": "16"}

            def __init__(self):
                self._blocks = [b"MZfirst", b"second", b""]

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self, _size):
                return self._blocks.pop(0)

        original_urlopen = updates.urllib.request.urlopen
        updates.urllib.request.urlopen = lambda *_args, **_kwargs: Response()
        cancelled = {"value": False}
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                with self.assertRaises(updates.UpdateDownloadCancelled):
                    updates.download_update_installer(
                        "https://download.example/NTE-Drive-Calc.exe",
                        destination_dir=temp_dir,
                        progress_callback=lambda *_args: cancelled.__setitem__("value", True),
                        cancel_check=lambda: cancelled["value"],
                    )
                self.assertEqual([], list(Path(temp_dir).iterdir()))
        finally:
            updates.urllib.request.urlopen = original_urlopen

    def test_update_check_uses_mirror_without_cdk_and_keeps_github_release_link(self):
        from src.ui.controllers import update_controller

        captured = {}
        original_fetch = update_controller.fetch_update_info

        def fake_fetch(api_url, version, *, cdk=""):
            captured.update(api_url=api_url, version=version, cdk=cdk)
            return {"has_release": True, "latest": "v2.0.1"}

        class Window:
            pass

        update_controller.fetch_update_info = fake_fetch
        try:
            info = update_controller._fetch_update_info(Window())
        finally:
            update_controller.fetch_update_info = original_fetch

        self.assertEqual("", captured["cdk"])
        self.assertIn("mirrorchyan.com/api/resources/NTE-Drive-Calc/latest", captured["api_url"])
        self.assertTrue(info["release_url"].endswith("/releases"))





if __name__ == "__main__":

    unittest.main()
