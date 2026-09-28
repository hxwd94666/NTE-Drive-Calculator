# 固化轴分页 complete 字段缺失时的判定，锁住两条路径的差异（N2 举证前不改行为）。
"""Pin the ``complete`` interpretation of battle-axis pages.

同一字段在不同层有不同默认值：

* ``integrations/nte_core_battle.parse_battle_axis`` → 缺失按「已完成」（True）
* ``storage/sqlite/battle_axis_dao`` → 缺失按「已完成」（True）
* ``storage/sqlite/battle_axis_finalization_dao`` → 缺失按「未完成」（False）

分页终止条件由解析层的默认值决定，因此这里的断言是该行为的唯一固化点。
改为一致之前需要实机/上游协议证据（设备是否总会发送 ``complete``），
详见 ``docs/audit-report.md`` 的 N2。
"""

from __future__ import annotations

import unittest

from src.integrations.nte_core_battle import parse_battle_axis


def _page(**overrides: object) -> dict[str, object]:
    page: dict[str, object] = {
        "contract_version": 1,
        "battle_record_id": "record-1",
        "generation": "1",
        "rows": [],
    }
    page.update(overrides)
    return page


class BattleAxisCompleteDefaultsTests(unittest.TestCase):
    def test_missing_complete_is_treated_as_complete(self) -> None:
        """缺 ``complete`` 时按已完成处理（当前分页终止行为）。"""

        self.assertIs(True, parse_battle_axis(_page())["complete"])

    def test_explicit_false_is_preserved(self) -> None:
        """显式 ``complete=False`` 必须保留，避免把未结束的分页当作已完成。"""

        self.assertIs(False, parse_battle_axis(_page(complete=False))["complete"])


if __name__ == "__main__":
    unittest.main()
