# 覆盖 JSON 原子保存的失败保留、读取契约和显式账号路径隔离。
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from src.storage.json_store import read_json, write_json


class JsonStoreTests(unittest.TestCase):
    def test_serialization_failure_preserves_existing_and_missing_targets(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_bytes(b'{"previous": true}')
            with self.assertRaises(TypeError):
                write_json(path, {"before": "新值", "bad": object()})
            self.assertEqual(b'{"previous": true}', path.read_bytes())
            missing = Path(directory) / "new" / "config.json"
            with self.assertRaises(TypeError):
                write_json(missing, {"bad": object()})
            self.assertFalse(missing.exists())
            self.assertEqual({path}, set(Path(directory).iterdir()))

    def test_flush_replace_and_write_errors_preserve_original_and_clean_stage(self):
        import tempfile as temp_module
        real_temporary = temp_module.NamedTemporaryFile

        class BrokenWrite:
            def __init__(self, *args, **kwargs):
                self.file = real_temporary(*args, **kwargs)
                self.name = self.file.name
            def __enter__(self):
                return self
            def __exit__(self, *args):
                self.file.close()
            def write(self, data):
                self.file.write(data[:1])
                raise OSError("write failed")

        for target, replacement in (("os.fsync", OSError("flush failed")),
                                    ("os.replace", OSError("replace failed")),
                                    ("tempfile.NamedTemporaryFile", BrokenWrite)):
            with self.subTest(target=target), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "config.json"
                path.write_bytes(b'{"previous": true}')
                with patch(target, side_effect=replacement):
                    with self.assertRaises(OSError):
                        write_json(path, {"next": "新值"})
                self.assertEqual(b'{"previous": true}', path.read_bytes())
                self.assertEqual({path}, set(Path(directory).iterdir()))

    def test_utf8_indent_and_explicit_account_paths_remain_independent(self):
        with tempfile.TemporaryDirectory() as directory:
            a, b = (Path(directory) / account / "settings.json" for account in ("a", "b"))
            value = {"名称": "配置", "items": [1, 2]}
            write_json(a, value, indent=4)
            original_a = a.read_bytes()
            write_json(b, {"名称": "另一个账号"})
            self.assertEqual(json.dumps(value, ensure_ascii=False, indent=4), a.read_text("utf-8"))
            self.assertEqual(value, read_json(a))
            self.assertEqual({"名称": "另一个账号"}, read_json(b))
            self.assertEqual(original_a, a.read_bytes())
            self.assertEqual([a], list(a.parent.iterdir()))
            self.assertEqual([b], list(b.parent.iterdir()))

    def test_read_default_only_for_missing_and_errors_still_propagate(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            default = object()
            self.assertIs(default, read_json(path, default))
            path.write_text("{", encoding="utf-8")
            with self.assertRaises(json.JSONDecodeError):
                read_json(path, default)
            with patch("builtins.open", side_effect=PermissionError("denied")):
                with self.assertRaises(PermissionError):
                    read_json(path, default)

    def test_scanning_config_keeps_explicit_paths_and_propagates_failed_save(self):
        from src.features.scanning import post_action_dialog as consumer
        with tempfile.TemporaryDirectory() as directory:
            a, b = (Path(directory) / name for name in ("a", "b"))
            consumer.save_scan_post_action_config(a, {})
            path = consumer.scan_post_action_config_path(a)
            old = path.read_bytes()
            consumer.save_scan_post_action_config(b, {})
            with patch("os.replace", side_effect=OSError("replace denied")):
                with self.assertRaises(OSError):
                    consumer.save_scan_post_action_config(a, {})
            self.assertEqual(old, path.read_bytes())
            self.assertEqual(read_json(path), read_json(consumer.scan_post_action_config_path(b)))
            self.assertEqual([path], list(a.iterdir()))
