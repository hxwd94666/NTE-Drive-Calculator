# 覆盖安装级角色实例缓存的账号隔离和迁移行为。
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.services.character_instance_cache import CharacterInstanceCache


class CharacterInstanceCacheTests(unittest.TestCase):
    def test_cache_isolated_by_application_account(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with CharacterInstanceCache(Path(temporary) / "shared.sqlite3") as cache:
                cache.upsert("account-a", 1055, {"slot": 1, "serial": 2}, source="snapshot")
                cache.upsert("account-b", 1055, {"slot": 3, "serial": 4}, source="snapshot")
                self.assertEqual({"slot": 1, "serial": 2}, cache.get("account-a", 1055))
                self.assertEqual({"slot": 3, "serial": 4}, cache.get("account-b", 1055))


if __name__ == "__main__":
    unittest.main()
