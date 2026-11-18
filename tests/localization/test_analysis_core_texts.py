# 验证分析组件中文文案同步：Rust 占位符转命名参数、字面花括号转义，以及登记后在显示边界被投影。
from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication, QLabel

from src.domain.localization.catalog import make_catalog
from src.integrations.preferences.locale_files import bundled_text_catalog, read_catalog_file
from src.integrations.runtime.bundled_resources import bundled_root
from src.ui.common import display_projection
from tools.i18n import sync_analysis_core_texts as sync

NTE_TEST_TIER = "core"


@pytest.mark.parametrize("rust,expected", [
    ("暴击率上限 {}% 低于基础暴击率 5%", "暴击率上限 {v1}% 低于基础暴击率 5%"),
    ("已查候选暴击率最高 {:.1}", "已查候选暴击率最高 {v1}"),
    ("把正式浊燃记录 {damage} 替换为 {id}", "把正式浊燃记录 {damage} 替换为 {id}"),
    ("第 {0} 层与第 {1} 层", "第 {v1} 层与第 {v2} 层"),
    ("条件 {key} 尚未支持", "条件 {key_value} 尚未支持"),
])
def test_rust_placeholders_become_named_parameters(rust, expected):
    assert sync._template(rust)[0] == expected


def test_literal_braces_are_escaped_not_treated_as_parameters():
    entries = sync.plan(["有效系数 ∈ {1, 正式层数}"], {})
    (template,) = entries.values()
    assert template == "有效系数 ∈ {{1, 正式层数}}"


def test_registered_component_template_is_projected_at_display():
    QApplication.instance() or QApplication([])
    base = bundled_text_catalog()
    key = next(k for k in sorted(base.messages) if k.startswith("analysis_core.template.")
               and len(base.parameters[k]) == 1)
    (name,) = tuple(base.parameters[key])
    catalog = make_catalog(read_catalog_file(bundled_root() / "docs/examples/locales/qps-ploc.json"), base=base)
    display_projection.set_display_catalog(catalog)
    try:
        # 组件按 Rust 原模板生成的中文整行，在显示时套用同 key 译文。
        assert QLabel(base.render(key, **{name: "12.5"})).text() == catalog.render(key, **{name: "12.5"})
    finally:
        display_projection.set_display_catalog(None)
