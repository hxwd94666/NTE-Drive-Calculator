# 展示本次 DLL 与抓包采集的独立结果，明确计时口径和差值分母。
from __future__ import annotations

from src.ui.common.text_catalog import plain_label, resolve_text_catalog
import math

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QGridLayout, QLabel, QVBoxLayout

from src.domain.battle_report.capture.state import BattleCaptureComparisonState
from src.domain.battle_report.capture.state import BattleCaptureState


_CLOCK_NAMES = {"wall_clock": "真实经过时间", "subtract_time_stop": "扣除停表时间"}
_PHASE_NAMES = {
    "idle": "未开始", "starting": "启动中", "running": "采集中",
    "stopping": "正在结束", "stopped": "已结束", "error": "采集异常",
}


def _number(value: float, decimals: int = 0) -> str:
    return f"{value:,.{decimals}f}" if math.isfinite(value) else "—"


class BattleCaptureComparisonPanel(QFrame):
    def __init__(self, parent=None, *, text_catalog=None) -> None:
        super().__init__(parent)
        self.text_catalog = resolve_text_catalog(text_catalog)
        self.setObjectName("card")
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 16)
        title = plain_label(resolve_text_catalog(text_catalog).render('ui.battle_report.init.title'), self)
        title.setObjectName("cardTitle")
        root.addWidget(title)
        self.notice = self._label()
        root.addWidget(self.notice)
        grid = QGridLayout()
        grid.setHorizontalSpacing(24)
        grid.setVerticalSpacing(8)
        grid.addWidget(plain_label("DLL", self), 0, 1)
        grid.addWidget(plain_label(resolve_text_catalog(text_catalog).render('ui.battle_report.init.caption_9'), self), 0, 2)
        self.values: dict[str, dict[str, QLabel]] = {"native": {}, "packet": {}}
        for row, (key, name) in enumerate((
            ("damage", "总伤害"), ("records", "记录数"), ("duration", "时长"),
            ("clock", "计时方式"), ("dps", "DPS"), ("status", "状态"),
            ("record_id", "战报编号"),
        ), 1):
            grid.addWidget(plain_label(name, self), row, 0)
            for column, source in enumerate(("native", "packet"), 1):
                value = self._label()
                value.setObjectName(f"comparison_{source}_{key}")
                self.values[source][key] = value
                grid.addWidget(value, row, column)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(2, 1)
        root.addLayout(grid)
        self.damage_difference = self._label()
        self.damage_ratio = self._label()
        self.dps_difference = self._label()
        for label in (self.damage_difference, self.damage_ratio, self.dps_difference):
            root.addWidget(label)
        self.set_snapshot(None)

    def _label(self) -> QLabel:
        label = plain_label(self)
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        label.setWordWrap(True)
        return label

    def _render_source(self, source: str, state: BattleCaptureState) -> None:
        labels = self.values[source]
        summary = state.summary
        labels["damage"].setText(_number(summary.total_damage) if summary else "—")
        labels["records"].setText(f"{summary.total_hits:,}" if summary else "—")
        labels["duration"].setText(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.battle_report.render_source.labels_duration', value=f'{_number(summary.duration_seconds, 2)}') if summary else "—")
        labels["clock"].setText(
            _CLOCK_NAMES.get(summary.dps_time_mode, f"未识别（{summary.dps_time_mode}）")
            if summary else resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.battle_report.render_source.labels_clock')
        )
        labels["dps"].setText(_number(summary.total_dps, 2) if summary else "—")
        status = _PHASE_NAMES.get(state.phase, state.phase)
        details = state.error or state.message
        labels["status"].setText(f"{status} · {details}" if details else status)
        labels["record_id"].setText(
            str(state.battle_record_id) if state.battle_record_id is not None else resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.battle_report.render_source.labels_record_id')
        )

    def set_snapshot(self, snapshot: BattleCaptureComparisonState | None) -> None:
        if snapshot is None:
            for labels in self.values.values():
                for label in labels.values():
                    label.clear()
            for label in (self.notice, self.damage_difference, self.damage_ratio, self.dps_difference):
                label.clear()
            self.hide()
            return
        self.show()
        self._render_source("native", snapshot.native)
        self._render_source("packet", snapshot.packet)
        prefix = ("本次对照已中断" if snapshot.interrupted else
                  "本次对照已结束" if snapshot.finished else
                  "正在结束本次对照" if snapshot.stop_requested else "本次对照进行中")
        self.notice.setText(
            prefix + resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.battle_report.set_snapshot.notice')
        )
        native, packet = snapshot.native.summary, snapshot.packet.summary
        if native is None or packet is None:
            self.damage_difference.setText(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.battle_report.set_snapshot.damage_difference_2'))
            self.damage_ratio.setText(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.battle_report.set_snapshot.damage_ratio_3'))
            self.dps_difference.setText(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.battle_report.set_snapshot.dps_difference'))
            return
        difference = native.total_damage - packet.total_damage
        self.damage_difference.setText(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.battle_report.set_snapshot.damage_difference', value=f'{_number(difference)}'))
        ratio = native.total_damage / packet.total_damage if packet.total_damage > 0 else None
        self.damage_ratio.setText(
            resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.battle_report.set_snapshot.damage_ratio', value=f'{_number(ratio * 100, 2)}')
            if ratio is not None else resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.battle_report.set_snapshot.damage_ratio_2')
        )
        if snapshot.interrupted or snapshot.native.error or snapshot.packet.error:
            self.dps_difference.setText(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.battle_report.set_snapshot.dps_difference_2'))
        elif any(state.phase not in {"running", "stopping", "stopped"} for state in (snapshot.native, snapshot.packet)):
            self.dps_difference.setText(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.battle_report.set_snapshot.dps_difference_3'))
        elif native.dps_time_mode != packet.dps_time_mode:
            self.dps_difference.setText(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.battle_report.set_snapshot.dps_difference_4'))
        elif native.dps_time_mode not in _CLOCK_NAMES or min(native.duration_seconds, packet.duration_seconds) <= 0:
            self.dps_difference.setText(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.battle_report.set_snapshot.dps_difference_5'))
        else:
            self.dps_difference.setText(
                resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.battle_report.set_snapshot.dps_difference_6', value=f'{_number(native.total_dps - packet.total_dps, 2)}')
            )
