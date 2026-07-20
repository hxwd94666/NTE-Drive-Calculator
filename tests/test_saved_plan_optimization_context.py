# 验证保存方案优化会加载角色功能的面板上下文。
"""Tests for the saved-plan replacement context loader."""

from __future__ import annotations

import unittest

from src.features.inventory.page import _saved_plan_optimization_role_context


class SavedPlanOptimizationContextTests(unittest.TestCase):
    def test_refreshes_role_context_when_not_loaded(self) -> None:
        class Window:
            _my_role_dirty = False
            _my_role_form_data = None

            def _refresh_my_role(self):
                self._my_role_form_data = {"主角": {"sub_stats": {"攻击力白值": 1145}}}

        context = _saved_plan_optimization_role_context(Window(), "主角")
        self.assertEqual(1145, context["sub_stats"]["攻击力白值"])

    def test_preserves_unsaved_role_context(self) -> None:
        class Window:
            _my_role_dirty = True
            _my_role_form_data = {"主角": {"sub_stats": {"攻击力白值": 1200}}}
            refreshed = False

            def _refresh_my_role(self):
                self.refreshed = True

            def _flush_role_widgets(self):
                pass

        window = Window()
        context = _saved_plan_optimization_role_context(window, "主角")
        self.assertEqual(1200, context["sub_stats"]["攻击力白值"])
        self.assertFalse(window.refreshed)


if __name__ == "__main__":
    unittest.main()
