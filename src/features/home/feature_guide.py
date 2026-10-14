# 以无音效的普通弹窗展示工作台功能入口与简短操作说明。
from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Callable

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QVBoxLayout, QWidget,
)

from src.app.theme import themed_style
from src.app.window_geometry import fit_dialog_to_available_screen


@dataclass(frozen=True)
class GuideSection:
    title: str
    lines: tuple[str, ...]
    numbered: bool = False


@dataclass(frozen=True)
class FeatureGuide:
    title: str
    entry: str
    sections: tuple[GuideSection, ...]
    destinations: tuple[tuple[str, str], ...]


FEATURE_GUIDES = (
    FeatureGuide(
        "基础计算", "计算",
        (
            GuideSection("", (
                "先根据选择的风险模式获取数据。",
                "在「计算」选择角色优先级和分配设置。",
                "点击「开始计算」查看结果；满意后点击「保存配装」。",
            ), True),
            GuideSection("低风险", ("使用工作台-背包同步（推荐），或者计算-扫描模式。",)),
            GuideSection("中风险", ("使用工作台-同步游戏数据（推荐），相比背包同步可实时同步。",)),
        ),
        (("前往计算", "execute"),),
    ),
    FeatureGuide(
        "配装功能", "配装",
        (
            GuideSection("", (
                "确保已经在「计算」保存过方案。",
                "在「配装」选择角色、槽位，查看「计算配装」与「游戏配装」。",
                "需要游戏内装备时，右上角选择可用的装配方式。",
            ), True),
            GuideSection("低风险", ("可使用「自动装配」，耗时较长。",)),
            GuideSection("中风险", ("可使用「极速装配」，方便快捷（推荐）。",)),
        ),
        (("前往配装", "equipment"),),
    ),
    FeatureGuide(
        "弃置锁定", "低风险计算，中风险仓库（推荐）",
        (
            GuideSection("低风险", (
                "在「计算」中第一步选择全量扫描，点击「管理」。",
                "通过「管理」选择正常、锁定或弃置标准（记得开启）。",
                "核对目标后保存，在扫描完成后会缓慢进行弃置锁定。",
            ), True),
            GuideSection("中风险", (
                "在「仓库」可以选中空幕改动状态“弃置/锁定/正常”。",
                "通过右上角「管理」选择正常、锁定或弃置标准（记得开启）。",
                "核对目标后保存，游戏内空幕会快速完成弃置锁定。",
            ), True),
        ),
        (("前往计算", "execute"), ("前往仓库", "warehouse")),
    ),
    FeatureGuide(
        "空幕鉴定", "鉴定",
        (GuideSection("", (
            "选择空幕类型与品质。",
            "选择图片、粘贴、截图，或手工填写属性。",
            "点击「解析图片」或「开始鉴定」，查看属性和评分。",
        ), True),),
        (("前往鉴定", "identify"),),
    ),
    FeatureGuide(
        "倒带推荐", "工具 → 倒带推荐",
        (GuideSection("", (
            "选择目标角色、推荐方式和目标档位。",
            "点击「生成方案」，查看各位置的推荐。",
            "确认后可「保存方案」；如要帮忙倒带，可再选择「进行倒带」。",
        ), True),),
        (("前往工具", "toolbox"),),
    ),
    FeatureGuide(
        "边际计算", "战报 → 审计 → 边际计算",
        (GuideSection("", (
            "在「战报」打开一场可分析的已保存战报。",
            "在「审计」点击「边际计算」，选择分析角色并调整候选配置。",
            "点击「重算」比较当前与候选伤害；结果是固定轴估计，不改实测战报。",
        ), True),),
        (("前往战报", "battle_report"),),
    ),
)


class FeatureGuideDialog(QDialog):
    """普通帮助使用无系统提示音的 QDialog，导航仅消费显式点击。"""

    def __init__(self, parent: QWidget, guide: FeatureGuide, navigate: Callable[[str], None]):
        super().__init__(parent)
        self.setObjectName("featureGuideDialog")
        self.setWindowTitle(guide.title + " · 功能说明")
        self.setWindowModality(Qt.WindowModal)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 16)
        layout.setSpacing(14)

        heading = QLabel(guide.title, self)
        heading.setObjectName("featureGuideDialogTitle")
        heading.setStyleSheet(themed_style("font-size:18px;font-weight:700;color:#f0f6fc"))
        layout.addWidget(heading)
        entry = QLabel("入口：" + guide.entry, self)
        entry.setObjectName("featureGuideEntry")
        entry.setWordWrap(True)
        layout.addWidget(entry)

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        content = QWidget(scroll)
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 8, 0)
        content_layout.setSpacing(10)
        for section in guide.sections:
            if section.title:
                title = QLabel(section.title + "：", content)
                title.setStyleSheet(themed_style("font-weight:700;color:#58a6ff"))
                content_layout.addWidget(title)
            for index, line in enumerate(section.lines, 1):
                label = QLabel(f"{index}. {line}" if section.numbered else line, content)
                label.setWordWrap(True)
                label.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard)
                content_layout.addWidget(label)
        content_layout.addStretch()
        scroll.setWidget(content)
        layout.addWidget(scroll, 1)

        actions = QHBoxLayout()
        actions.addStretch()
        for text, key in guide.destinations:
            button = QPushButton(text, self)
            button.clicked.connect(lambda _checked=False, target=key: self._navigate(navigate, target))
            actions.addWidget(button)
        close = QPushButton("关闭", self)
        close.setDefault(True)
        close.clicked.connect(self.reject)
        actions.addWidget(close)
        layout.addLayout(actions)
        if guide.title in {"弃置锁定", "空幕鉴定", "倒带推荐", "边际计算"}:
            self._fit_compact_content(layout, heading, entry, scroll, content_layout, actions)
        else:
            fit_dialog_to_available_screen(self, QSize(650, 360))

    def _fit_compact_content(self, layout, heading, entry, scroll, content_layout, actions):
        """先约束宽度，再按实际换行测高度；长说明仍由原正文视口滚动。"""
        self.ensurePolished()
        layout.activate()
        margins = layout.contentsMargins()
        labels = [heading, entry, *scroll.widget().findChildren(QLabel)]
        natural_width = max(label.fontMetrics().horizontalAdvance(label.text()) for label in labels)
        preferred_width = max(440, min(650, natural_width + margins.left() + margins.right() + 8))
        fitted = fit_dialog_to_available_screen(self, QSize(preferred_width, 360))
        # 按实际内容宽度测量，标题与入口不受正文滚动条影响。
        body_width = max(1, self.contentsRect().width() - margins.left() - margins.right())
        # QScrollArea 不向外传播正文的 heightForWidth，不能拿它的固定 sizeHint 猜空白。
        # 预留主题滚动条宽度，避免初次布局按旧 sizeHint 先加滚动条、再挤窄正文的循环。
        text_width = max(1, body_width - 2 * scroll.frameWidth() - scroll.verticalScrollBar().sizeHint().width())
        content_height = content_layout.heightForWidth(text_width)
        if content_height < 0:
            content_height = content_layout.sizeHint().height()
        entry_height = entry.heightForWidth(body_width)
        if entry_height < 0:
            entry_height = entry.sizeHint().height()
        height = (margins.top() + margins.bottom() + 3 * layout.spacing()
                  + heading.sizeHint().height() + entry_height + actions.sizeHint().height()
                  + content_height + 2 * scroll.frameWidth()
                  + self.height() - self.contentsRect().height())
        fit_dialog_to_available_screen(self, QSize(fitted.width(), height))

    def _navigate(self, navigate: Callable[[str], None], key: str) -> None:
        self.accept()
        navigate(key)


def add_feature_guides(layout: QVBoxLayout, parent: QWidget, navigate: Callable[[str], None]) -> None:
    """只打开功能说明；不通过帮助按钮启动同步、装配或其他业务。"""
    row = QHBoxLayout()
    row.setSpacing(10)
    for guide in FEATURE_GUIDES:
        button = QPushButton(guide.title, parent)
        button.setObjectName("featureGuideButton")
        button.setAccessibleName(guide.title + "说明")
        button.setFixedWidth(88)
        button.clicked.connect(
            lambda _checked=False, item=guide: FeatureGuideDialog(parent, item, navigate).exec()
        )
        row.addWidget(button)
    row.addStretch()
    layout.addLayout(row)
