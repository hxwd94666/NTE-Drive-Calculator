# 展示本次 DLL 与抓包采集的独立结果，明确计时口径和差值分母。
from __future__ import annotations

import math

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QGridLayout, QLabel, QVBoxLayout

from src.domain.battle_capture_comparison import BattleCaptureComparisonState
from src.domain.battle_report import BattleCaptureState


_CLOCK_NAMES = {"wall_clock": "真实经过时间", "subtract_time_stop": "扣除停表时间"}
_PHASE_NAMES = {
    "idle": "未开始", "starting": "启动中", "running": "采集中",
    "stopping": "正在结束", "stopped": "已结束", "error": "采集异常",
}


def _number(value: float, decimals: int = 0) -> str:
    return f"{value:,.{decimals}f}" if math.isfinite(value) else "—"


class BattleCaptureComparisonPanel(QFrame):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("card")
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 16)
        title = QLabel("本次双路采集对照", self)
        title.setObjectName("cardTitle")
        root.addWidget(title)
        self.notice = self._label()
        root.addWidget(self.notice)
        grid = QGridLayout()
        grid.setHorizontalSpacing(24)
        grid.setVerticalSpacing(8)
        grid.addWidget(QLabel("DLL", self), 0, 1)
        grid.addWidget(QLabel("抓包", self), 0, 2)
        self.values: dict[str, dict[str, QLabel]] = {"native": {}, "packet": {}}
        for row, (key, name) in enumerate((
            ("damage", "总伤害"), ("records", "记录数"), ("duration", "时长"),
            ("clock", "计时方式"), ("dps", "DPS"), ("status", "状态"),
            ("record_id", "战报编号"),
        ), 1):
            grid.addWidget(QLabel(name, self), row, 0)
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
        label = QLabel(self)
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        label.setWordWrap(True)
        return label

    def _render_source(self, source: str, state: BattleCaptureState) -> None:
        labels = self.values[source]
        summary = state.summary
        labels["damage"].setText(_number(summary.total_damage) if summary else "—")
        labels["records"].setText(f"{summary.total_hits:,}" if summary else "—")
        labels["duration"].setText(f"{_number(summary.duration_seconds, 2)} 秒" if summary else "—")
        labels["clock"].setText(
            _CLOCK_NAMES.get(summary.dps_time_mode, f"未识别（{summary.dps_time_mode}）")
            if summary else "等待采样"
        )
        labels["dps"].setText(_number(summary.total_dps, 2) if summary else "—")
        status = _PHASE_NAMES.get(state.phase, state.phase)
        details = state.error or state.message
        labels["status"].setText(f"{status} · {details}" if details else status)
        labels["record_id"].setText(
            str(state.battle_record_id) if state.battle_record_id is not None else "尚未保存"
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
            prefix + "；两路独立记录，各用自己的采样时段。结束不代表来源覆盖完整。"
            "记录数可能包含受击记录，不等于造成伤害的命中数。"
        )
        native, packet = snapshot.native.summary, snapshot.packet.summary
        if native is None or packet is None:
            self.damage_difference.setText("总伤差：等待双方采样")
            self.damage_ratio.setText("DLL / 抓包总伤比例：等待双方采样")
            self.dps_difference.setText("DPS 差：等待双方采样")
            return
        difference = native.total_damage - packet.total_damage
        self.damage_difference.setText(f"已收到总伤差（DLL − 抓包）：{_number(difference)}")
        ratio = native.total_damage / packet.total_damage if packet.total_damage > 0 else None
        self.damage_ratio.setText(
            f"DLL / 抓包总伤比例：{_number(ratio * 100, 2)}%"
            if ratio is not None else "DLL / 抓包总伤比例：未定义（抓包总伤为 0）"
        )
        if snapshot.interrupted or snapshot.native.error or snapshot.packet.error:
            self.dps_difference.setText("DPS 差：对照中断或存在异常，保留双方实际值")
        elif any(state.phase not in {"running", "stopping", "stopped"} for state in (snapshot.native, snapshot.packet)):
            self.dps_difference.setText("DPS 差：等待双方采集就绪")
        elif native.dps_time_mode != packet.dps_time_mode:
            self.dps_difference.setText("DPS 差：计时方式不同，不作直接比较")
        elif native.dps_time_mode not in _CLOCK_NAMES or min(native.duration_seconds, packet.duration_seconds) <= 0:
            self.dps_difference.setText("DPS 差：等待双方有效计时")
        else:
            self.dps_difference.setText(
                f"DPS 差（DLL − 抓包，各自采样时段）：{_number(native.total_dps - packet.total_dps, 2)}"
            )
