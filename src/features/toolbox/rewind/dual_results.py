# 兼顾模式结果：结果区只渲染八槽；两组重合的缺口形状经“共同缺口”按钮只读查看。
from collections import Counter

from PySide6.QtWidgets import QPushButton

from src.features.toolbox.rewind.dual_overlap_dialog import RewindDualOverlapDialog
from src.ui.common.text_catalog import plain_tooltip


class RewindDualResultsMixin:
    def _render_dual_results(self, analysis, *, preserve_edits=False):
        result, text = analysis.dual_target, self.text_catalog.render
        self._dual_manual_draft = False
        self._dual_results_edited = preserve_edits
        if not preserve_edits:
            self._set_replacement_inventory_counts(dict(analysis.owned_shape_counts))
            self._refresh_candidate_gaps(analysis, dual=True)
            self._apply_recommendations(analysis.recommendations)
        detail = text('rewind.dual.no_shared') if not result.shared and not preserve_edits else text('rewind.dual.heuristic')
        self._render_rewind_slots(text('rewind.dual.result_title'), detail)

    def _dual_overlap_analysis(self):
        analysis = self._current_margin_analysis()
        return analysis if analysis is not None and analysis.dual_target is not None else None

    def _make_dual_overlap_button(self):
        button = QPushButton(self.text_catalog.render('rewind.dual.overlap.button'))
        button.setObjectName('rewindDualOverlap')
        button.clicked.connect(self._show_dual_overlap)
        self._sync_dual_overlap_button(button)
        return button

    def _sync_dual_overlap_button(self, *buttons):
        available = self._dual_overlap_analysis() is not None
        tip = '' if available else plain_tooltip(self.text_catalog.render('rewind.dual.overlap.unavailable'))
        for button in buttons or self.findChildren(QPushButton, 'rewindDualOverlap'):
            button.setEnabled(available)
            button.setToolTip(tip)

    def _show_dual_overlap(self):
        analysis = self._dual_overlap_analysis()
        if analysis is None:
            return
        quantities = Counter(slot.shape.shape_id for slot in self._editable_slots if slot is not None)
        dialog = RewindDualOverlapDialog(self, analysis=analysis, quantities=quantities,
                                         edited=getattr(self, '_dual_results_edited', False),
                                         text_catalog=self.text_catalog)
        try:
            dialog.exec()
        finally:
            dialog.deleteLater()
