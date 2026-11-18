# 验证倒带执行弹窗的十连次数上限：随品质启用、只提交已选品质的正整数，并随策略偏好保存。
from PySide6.QtWidgets import QApplication, QDialog, QLabel, QSpinBox

from src.features.toolbox.rewind import execution_ui as rewind_execution_ui
from src.features.toolbox.rewind.execution_dialog import RewindExecutionDialog, RewindExecutionOptions
from tests.rewind.preferences_fixture import ProfileServiceFixture
from tests.support.qt_fonts import fonts


def limits(dialog):
    return {spin.property('rewindValue'): spin for spin in dialog.findChildren(QSpinBox, 'rewindTenLimit')}


def test_limit_inputs_follow_their_quality_and_submit_positive_values_only():
    fonts()
    dialog = RewindExecutionDialog(None, initial=RewindExecutionOptions(('gold',), 'none', (('gold', 4), ('blue', 2))))
    try:
        spins = limits(dialog)
        assert set(spins) == {'blue', 'purple', 'gold'}
        assert [spins[key].isEnabled() for key in ('blue', 'purple', 'gold')] == [False, False, True]
        assert spins['gold'].value() == 4
        dialog._quality_buttons['blue'].setChecked(True)
        assert spins['blue'].isEnabled() and spins['blue'].value() == 2
        spins['blue'].setValue(0)
        dialog._quality_buttons['purple'].setChecked(True)
        spins['purple'].setValue(7)
        dialog._quality_buttons['purple'].setChecked(False)
        assert not spins['purple'].isEnabled()
        # 未选品质与 0（按余额）都不提交上限。
        assert dialog.options().ten_limits == (('gold', 4),)
        assert dialog.findChild(QLabel, 'rewindStopNotice').text().startswith('只进行十连抽取，')
        texts = [label.text() for label in dialog.findChildren(QLabel)]
        # 次数区与品质、驱动定制一样有独立标题；说明只放在悬浮提示，不再占一行正文。
        assert '抽取次数（仅十连）' in texts and not any(text.startswith('次数为') for text in texts)
        assert spins['blue'].specialValueText() == '默认' and '不填或 0' in spins['blue'].toolTip()
        assert spins['blue'].buttonSymbols() == QSpinBox.NoButtons
    finally:
        dialog.deleteLater()


def test_limits_are_saved_with_execution_options_and_restored(monkeypatch):
    from src.features.toolbox.page import _RewindRecommendationDialog

    QApplication.instance() or QApplication([])

    class Service:
        def __init__(self):
            self.saved = {'target_character_ids': [1004]}

        def load_preferences(self):
            return dict(self.saved)

        def save_preferences(self, value):
            self.saved = dict(value)

    class AcceptedDialog:
        def __init__(self, *_args, **_kwargs):
            pass

        def exec(self):
            return QDialog.DialogCode.Accepted

        def options(self):
            return RewindExecutionOptions(('purple', 'gold'), 'none', (('gold', 3),))

    service = Service()
    dialog = _RewindRecommendationDialog(ProfileServiceFixture(service), None, operation_entry=lambda *_: True)
    monkeypatch.setattr(rewind_execution_ui, 'RewindExecutionDialog', AcceptedDialog)
    monkeypatch.setattr(dialog, '_start_rewind_execution', lambda: None)
    try:
        dialog._configure_rewind()
        assert service.saved['rewind_ten_limits'] == {'gold': 3}
        service.saved['rewind_ten_limits'] = {'gold': 3, 'blue': 0, 'purple': 'x', 'red': 2}
        reopened = _RewindRecommendationDialog(ProfileServiceFixture(service), None, operation_entry=lambda *_: True)
        try:
            # 损坏或无效的上限按“不填”处理，不阻断打开窗口。
            assert reopened._rewind_options.ten_limits == (('gold', 3),)
        finally:
            reopened.deleteLater()
    finally:
        dialog.deleteLater()
