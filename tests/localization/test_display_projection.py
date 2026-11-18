# 验证显示边界投影：只替换已登记的整句原文，中文目录为空操作，业务原文与未登记文本保持不变。
from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication, QComboBox, QLabel, QPushButton, QTableWidgetItem

from src.domain.localization.catalog import make_catalog
from src.integrations.preferences.locale_files import bundled_text_catalog, read_catalog_file
from src.integrations.runtime.bundled_resources import bundled_root
from src.ui.common import display_projection
from src.ui.common.text_catalog import TextActionButton, plain_label, plain_tooltip

NTE_TEST_TIER = "core"


@pytest.fixture
def patch_catalog():
    QApplication.instance() or QApplication([])
    base = bundled_text_catalog()
    catalog = make_catalog(read_catalog_file(bundled_root() / "docs/examples/locales/qps-ploc.json"), base=base)
    display_projection.set_display_catalog(catalog)
    yield base, catalog
    # 投影是进程级显示状态，用例结束必须恢复为中文空操作。
    display_projection.set_display_catalog(None)


def _registered(base):
    return next(text for key, text in sorted(base.messages.items())
                if key.startswith("common.") and not base.parameters[key] and "\n" not in text)


def test_registered_whole_text_is_projected_in_widgets(patch_catalog):
    base, catalog = patch_catalog
    text = _registered(base)
    expected = display_projection.project(text)
    assert expected != text
    label = QLabel(text)
    button = QPushButton()
    button.setText(text)
    combo = QComboBox()
    combo.addItems([text])
    item = QTableWidgetItem(text)
    assert label.text() == expected and button.text() == expected
    assert combo.itemText(0) == expected and item.text() == expected
    assert plain_label(text).text() == expected
    assert TextActionButton(text).accessibleName() == expected
    assert expected.replace("<", "&lt;") in plain_tooltip(text) or expected in plain_tooltip(text)


def test_unregistered_and_partial_text_is_untouched(patch_catalog):
    base, _catalog = patch_catalog
    text = _registered(base)
    for value in ("未登记的诊断原文 #42", f"前缀{text}", "HTGame.exe"):
        assert QLabel(value).text() == value


def test_multiline_text_projects_registered_lines_only(patch_catalog):
    base, _catalog = patch_catalog
    text = _registered(base)
    value = f"{text}\n未登记行"
    assert display_projection.project(value) == f"{display_projection.project(text)}\n未登记行"


def test_chinese_catalog_disables_projection():
    QApplication.instance() or QApplication([])
    display_projection.set_display_catalog(bundled_text_catalog())
    text = _registered(bundled_text_catalog())
    assert display_projection.project(text) == text
    assert QLabel(text).text() == text


def test_official_game_names_are_never_projected(patch_catalog):
    base, catalog = patch_catalog
    text = _registered(base)
    display_projection.set_display_catalog(catalog, excluded=frozenset({text}))
    assert QLabel(text).text() == text


def _template_key(base):
    # 取一条带单个参数、字面含足够汉字的发行模板，作为模板投影样本。
    return next(key for key, text in sorted(base.messages.items())
                if len(base.parameters[key]) == 1 and "\n" not in text
                and sum("一" <= ch <= "鿿" for ch in text.replace("{", "").replace("}", "")) >= 4)


def test_parameterized_template_line_is_projected_with_captured_value(patch_catalog):
    base, catalog = patch_catalog
    key = _template_key(base)
    (name,) = tuple(base.parameters[key])
    rendered = base.render(key, **{name: "42"})
    assert display_projection.project(rendered) == catalog.render(key, **{name: "42"})
    assert QLabel(rendered).text() == catalog.render(key, **{name: "42"})


def test_template_requires_whole_line_match(patch_catalog):
    base, _catalog = patch_catalog
    key = _template_key(base)
    (name,) = tuple(base.parameters[key])
    rendered = base.render(key, **{name: "42"})
    assert display_projection.project(f"前缀{rendered}") == f"前缀{rendered}"


def test_short_literal_templates_do_not_match(patch_catalog):
    # 字面汉字不足两个的模板（如“{a}：{b}”）不参与，避免把任意文本误判为模板。
    assert display_projection.project("甲：乙") == "甲：乙"
