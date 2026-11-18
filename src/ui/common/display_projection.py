# 在 Qt 显示边界把已登记的整句中文原文投影为当前界面语言；源码、数据与业务判断始终使用原文。
"""Display-boundary projection for registered Chinese UI text.

规则：
- 只在文字交给 Qt 控件显示的那一刻替换，判断、查表、持久化与日志仍使用中文原文；
- 只做整句精确匹配（多行文本逐行匹配），不做子串替换；未登记原文、游戏正式名称与诊断原文原样显示；
- 带参数的已登记模板（如“必需 {shape} 驱动不足”）按整行匹配：模板字面部分须含至少两个汉字，
  变量取出后先各自按整句规则投影，再套用当前目录的同 key 译文；多个模板同时命中时取字面最长者；
- 发行中文目录下为空操作，不改变任何显示；运行中冻结的目录由组合根启动时设置一次。
"""

from __future__ import annotations

from functools import lru_cache, wraps
import re
from string import Formatter

from PySide6.QtGui import QAction, QStandardItem
from PySide6.QtWidgets import (
    QAbstractButton, QCheckBox, QComboBox, QGroupBox, QLabel, QLineEdit, QListWidgetItem, QMenu, QMessageBox,
    QProgressDialog, QPushButton, QRadioButton, QTableWidget, QTableWidgetItem, QTabWidget, QToolButton,
    QTreeWidgetItem, QWidget,
)

from src.domain.localization.catalog import BASE_LOCALE, CatalogError, TextCatalog
from src.integrations.preferences.locale_files import bundled_text_catalog, literal_keys

_active: TextCatalog | None = None
_keys: dict[str, str] = {}
# (key, 整行正则, 最长字面片段, 字面长度)：按字面长度降序，先试更具体的模板。
_templates: list[tuple[str, re.Pattern[str], str, int]] = []
_installed = False
_CJK = re.compile(r"[\u4e00-\u9fff]")
_MIN_TEMPLATE_CJK = 2


def _compile_templates(excluded: frozenset[str]) -> list[tuple[str, re.Pattern[str], str, int]]:
    """把发行中文目录里的带参数条目编译成整行正则；字面过短的模板不参与，避免泛匹配。"""
    base = bundled_text_catalog()
    compiled = []
    for key, text in base.messages.items():
        if not base.parameters[key] or "\n" in text or text in excluded:
            continue
        try:
            parts = list(Formatter().parse(text))
        except ValueError:
            continue
        literal = "".join(part[0] for part in parts)
        if len(_CJK.findall(literal)) < _MIN_TEMPLATE_CJK:
            continue
        pattern, seen = "", set()
        for literal_part, field, _spec, _conversion in parts:
            pattern += re.escape(literal_part)
            if field is None:
                continue
            pattern += f"(?P={field})" if field in seen else f"(?P<{field}>.+?)"
            seen.add(field)
        chunk = max((part[0] for part in parts), key=len)
        compiled.append((key, re.compile(pattern + r"\Z"), chunk, len(literal)))
    compiled.sort(key=lambda row: -row[3])
    return compiled


def set_display_catalog(catalog: TextCatalog | None, *, excluded: frozenset[str] = frozenset()) -> None:
    """组合根在目录冻结后调用；中文目录或空值关闭投影。

    excluded 为正式游戏名称：即使与某条界面原文同字也不投影，保持游戏数据原样。
    """
    global _active, _keys, _templates
    _project_cached.cache_clear()
    if catalog is None or catalog.locale == BASE_LOCALE:
        _active, _keys, _templates = None, {}, []
        return
    _keys = {text: key for text, key in literal_keys().items() if text.strip() not in excluded}
    _templates = _compile_templates(excluded)
    _active = catalog
    _install_once()


def project(value):
    """整句精确匹配已登记原文，其次按带参数模板整行匹配；多行文本逐行处理，未登记的行保持原样。"""
    if _active is None or not isinstance(value, str) or not value:
        return value
    return _project_cached(value)


def _match_template(value: str) -> str | None:
    if "\n" in value or not _CJK.search(value):
        return None
    for key, regex, chunk, _length in _templates:
        if chunk and chunk not in value:
            continue
        match = regex.match(value)
        if match is None:
            continue
        # 变量本身可能是已登记的整句（如原因说明），先投影再填入译文模板。
        values = {name: _project_cached(text) for name, text in match.groupdict().items()}
        try:
            return _active.render(key, **values)
        except CatalogError:
            return None
    return None


@lru_cache(maxsize=8192)
def _project_cached(value: str) -> str:
    key = _keys.get(value)
    if key is not None:
        return _active.render(key)
    if "\n" in value:
        lines = value.split("\n")
        projected = [_project_cached(line) if line else line for line in lines]
        return "\n".join(projected) if projected != lines else value
    stripped = value.strip()
    if stripped != value and stripped in _keys:
        return value.replace(stripped, _active.render(_keys[stripped]), 1)
    templated = _match_template(stripped)
    if templated is not None:
        return value.replace(stripped, templated, 1) if stripped != value else templated
    return value


def _texts(values):
    return [project(v) for v in values] if isinstance(values, (list, tuple)) else values


def _wrap_args(original, positions):
    @wraps(original)
    def wrapper(self, *args, **kwargs):
        if _active is not None:
            args = tuple(project(a) if i in positions else a for i, a in enumerate(args))
        return original(self, *args, **kwargs)
    return wrapper


def _wrap_all_str(original):
    """构造函数：所有字符串位置参数都视作显示文本（Qt 构造参数中的字符串只有标题/文字）。"""
    @wraps(original)
    def wrapper(self, *args, **kwargs):
        if _active is not None:
            args = tuple(project(a) if isinstance(a, str) else _texts(a) if isinstance(a, list) else a for a in args)
        return original(self, *args, **kwargs)
    return wrapper


def _wrap_list(original, position):
    @wraps(original)
    def wrapper(self, *args, **kwargs):
        if _active is not None and len(args) > position:
            args = (*args[:position], _texts(args[position]), *args[position + 1:])
        return original(self, *args, **kwargs)
    return wrapper


def _wrap_static_box(original):
    # QMessageBox.warning(parent, title, text, ...)：标题与正文是第 2、3 个参数。
    @wraps(original)
    def wrapper(*args, **kwargs):
        if _active is not None:
            args = tuple(project(a) if i in (1, 2) else a for i, a in enumerate(args))
        return original(*args, **kwargs)
    return staticmethod(wrapper)


def _install_once() -> None:
    global _installed
    if _installed:
        return
    _installed = True
    for cls in (QLabel, QAbstractButton, QAction, QLineEdit, QMessageBox):
        cls.setText = _wrap_args(cls.setText, {0})
    for cls in (QLabel, QPushButton, QCheckBox, QRadioButton, QToolButton, QGroupBox, QAction, QMenu,
                QTableWidgetItem, QListWidgetItem, QTreeWidgetItem, QStandardItem, QProgressDialog, QMessageBox):
        cls.__init__ = _wrap_all_str(cls.__init__)
    QWidget.setToolTip = _wrap_args(QWidget.setToolTip, {0})
    QWidget.setWindowTitle = _wrap_args(QWidget.setWindowTitle, {0})
    QLineEdit.setPlaceholderText = _wrap_args(QLineEdit.setPlaceholderText, {0})
    QGroupBox.setTitle = _wrap_args(QGroupBox.setTitle, {0})
    QMenu.setTitle = _wrap_args(QMenu.setTitle, {0})
    QMenu.addAction = _wrap_args(QMenu.addAction, {0})
    QMenu.addMenu = _wrap_args(QMenu.addMenu, {0})
    QMessageBox.setInformativeText = _wrap_args(QMessageBox.setInformativeText, {0})
    QMessageBox.setDetailedText = _wrap_args(QMessageBox.setDetailedText, {0})
    for name in ("warning", "information", "critical", "question"):
        setattr(QMessageBox, name, _wrap_static_box(getattr(QMessageBox, name)))
    QProgressDialog.setLabelText = _wrap_args(QProgressDialog.setLabelText, {0})
    for cls in (QTableWidgetItem, QListWidgetItem, QStandardItem):
        cls.setText = _wrap_args(cls.setText, {0})
        cls.setToolTip = _wrap_args(cls.setToolTip, {0})
    QTreeWidgetItem.setText = _wrap_args(QTreeWidgetItem.setText, {1})
    QTreeWidgetItem.setToolTip = _wrap_args(QTreeWidgetItem.setToolTip, {1})
    QComboBox.addItem = _wrap_all_str(QComboBox.addItem)
    QComboBox.insertItem = _wrap_args(QComboBox.insertItem, {1})
    QComboBox.setItemText = _wrap_args(QComboBox.setItemText, {1})
    QComboBox.addItems = _wrap_list(QComboBox.addItems, 0)
    QTabWidget.addTab = _wrap_args(QTabWidget.addTab, {1, 2})
    QTabWidget.insertTab = _wrap_args(QTabWidget.insertTab, {2, 3})
    QTabWidget.setTabText = _wrap_args(QTabWidget.setTabText, {1})
    QTableWidget.setHorizontalHeaderLabels = _wrap_list(QTableWidget.setHorizontalHeaderLabels, 0)
    QTableWidget.setVerticalHeaderLabels = _wrap_list(QTableWidget.setVerticalHeaderLabels, 0)
