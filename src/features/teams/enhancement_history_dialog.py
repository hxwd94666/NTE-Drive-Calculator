# 只读展示本队冻结增强历史及联合详情，翻页不发起伤害分析，关闭等待本 owner 退场。
import json

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QListWidget, QPushButton, QTextEdit, QVBoxLayout

from src.app.window_geometry import fit_dialog_to_available_screen
from src.domain.teams.enhancement_presentation import team_enhancement_presentation
from src.features.teams.report_match_controller import ReportMatchController


class TeamEnhancementHistoryDialog(QDialog):
    def __init__(self, *, service, team, names=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("增强记录 · " + team.name)
        self._service, self._team, self._names = service, team, names or {}
        self._rows, self._offset, self._closing = [], 0, None
        self._reader = ReportMatchController(self)
        self._reader.result.connect(self._loaded)
        self._reader.error.connect(self._failed)
        self._reader.drained.connect(self._drained)
        root = QVBoxLayout(self)
        self.status = QLabel("正在读取增强记录…")
        self.status.setTextFormat(Qt.PlainText)
        self.status.setWordWrap(True)
        root.addWidget(self.status)
        self.list = QListWidget()
        self.list.setAccessibleName("队伍已保存增强历史")
        self.list.setWordWrap(True)
        self.list.currentRowChanged.connect(self._selected)
        root.addWidget(self.list, 1)
        self.details = QTextEdit()
        self.details.setReadOnly(True)
        self.details.setAccessibleName("冻结增强来源、覆盖与搜索详情")
        root.addWidget(self.details, 2)
        self.more = QPushButton("加载更多历史")
        self.more.clicked.connect(self._load)
        root.addWidget(self.more)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Close)
        self.buttons.rejected.connect(self.reject)
        root.addWidget(self.buttons)
        fit_dialog_to_available_screen(self, QSize(800, 730))
        QTimer.singleShot(0, self._load)

    def _load(self):
        if self._closing is not None or self._offset is None:
            return
        service, team, offset = self._service, self._team, self._offset
        self.more.setEnabled(False)
        self._reader.submit(lambda cancelled, publish: service.enhancement_history(team, offset=offset, cancelled=cancelled))

    def _loaded(self, result):
        rows, self._offset = result
        known = {row.result_id for row in self._rows}
        for row in rows:
            if row.result_id in known:
                continue
            self._rows.append(row)
            shown = team_enhancement_presentation(row.payload)
            historical = set(row.payload["character_ids"]) != set(self._team.character_ids)
            self.list.addItem(f"{row.created_at_utc} · {shown.headline}" + (" · 历史阵容" if historical else ""))
        self.status.setText(f"已加载 {len(self._rows)} 条记录，最新保存的在前。" if self._rows else
                            "暂无增强记录。整队预览保存后会显示在这里。")
        if self.list.currentRow() < 0 and self._rows:
            self.list.setCurrentRow(0)

    def _failed(self, message):
        self.status.setText("历史读取未完成；旧结果仍保留：" + (message or "输入已变化，请重新打开"))

    def _selected(self, index):
        if not 0 <= index < len(self._rows):
            return
        row = self._rows[index]
        payload = row.payload
        shown = team_enhancement_presentation(payload)
        lines = [shown.headline, shown.source, shown.coverage, shown.search,
                 "；".join(row.association), f"优化前计分：{payload['baseline_value'] if payload['baseline_value'] is not None else '未记录'}",
                 f"优化后计分：{payload['candidate_value'] if payload['candidate_value'] is not None else '未记录'}",
                 "保存时的成员方案："]
        for member in row.members:
            lines.append(f"{self._names.get(member['character_id'], '角色 '+str(member['character_id']))} · "
                         f"槽位 {member['slot_id']} · 方案 {member['plan_id']} · 来源快照 {member['source_snapshot_id']}")
        source = payload["provenance"]
        lines += ["", "静态来源：" + str(source.get("dataset_id") or "未记录"),
                  "模型／引擎：" + str(source.get("model_id") or "未记录") + " / " + str(source.get("engine_version") or "未记录"),
                  "结果 ID：" + row.result_id, "作业 ID：" + row.run_id]
        if row.joint_summary_json is not None:
            joint = json.loads(row.joint_summary_json)
            lines += ["", "联合详情：两队各自 B0 归一为 100，再等权比较；以下为指数，不是本队伤害。"]
            if joint["relative_gain"] is None:
                lines.append("联合作业未取得已证实的综合增幅。")
            else:
                lines.append(f"联合综合增幅：{joint['relative_gain']:+.2%}；归一指数 {joint['normalized_candidate']:.6g}。")
            for other in row.joint_results:
                text = team_enhancement_presentation(other.payload)
                names = "、".join(self._names.get(key, f"角色 {key}") for key in other.payload["character_ids"])
                lines.extend(("另一队：" + names, text.headline, text.coverage, text.search))
            if not row.joint_results:
                lines.append("另一队结果已删除或未保留；本队冻结记录及联合身份仍保留。")
        self.details.setPlainText("\n".join(lines))

    def is_running(self):
        return self._reader.is_running()

    def done(self, result):
        self._closing = result
        self._reader.close()
        if self.is_running():
            self.setEnabled(False)
        self._drained()

    def reject(self):
        self.done(QDialog.Rejected)

    def closeEvent(self, event):
        self.reject()
        event.ignore() if self.is_running() else event.accept()

    def _drained(self):
        if self._closing is not None and not self.is_running():
            super().done(self._closing)
        else:
            self.more.setEnabled(self._offset is not None and not self.is_running())
