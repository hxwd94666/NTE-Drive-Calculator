# 为手动功能入口提供受限说明和设置导航，不执行授权或业务操作。
from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import QApplication, QDialog, QDialogButtonBox, QLabel, QVBoxLayout

from src.app.window_geometry import fit_dialog_to_available_screen


_MODE_LABELS = {"offline": "离线", "low": "低风险", "medium": "中风险", "developer": "开发"}
_REQUIREMENTS = {
    "battle_capture": "请选择低风险模式使用抓包战报，或中风险模式使用 DLL 战报，并明确确认风险；开发模式可按所选来源采集。",
    "game_sync": "请选择低风险模式使用抓包同步，或中风险模式使用 DLL 同步，并明确确认风险。",
    "packet_capture": "需要低风险模式，或已启用抓包来源的开发模式，并明确确认风险。",
    "interface_input": "需要低风险、中风险或开发模式，并明确确认风险。",
    "native_load": "需要中风险模式，或已启用 DLL 来源的开发模式，并明确确认风险。",
    "native_sync": "需要中风险模式，或已启用 DLL 来源的开发模式，并明确确认风险。",
    "native_battle": "需要中风险模式，或已启用 DLL 来源的开发模式，并明确确认风险。",
    "native_equipment": "需要中风险模式，或已启用 DLL 来源的开发模式，并明确确认风险。",
    "diagnostics": "所有工作模式均可保存诊断信息；实际采集来源仍遵守当前模式。",
    "compare_sources": "需要选择开发模式，并明确确认风险；开发模式固定双路对照。",
}


def entry_is_allowed(policy, capability: str) -> bool:
    if capability in {"battle_capture", "game_sync"}:
        native = "native_battle" if capability == "battle_capture" else "native_sync"
        return policy.allowed(native) or policy.allowed("packet_capture")
    return policy.allowed(capability)


def _belongs_to(widget, owner) -> bool:
    if owner is None:
        return False
    parent = widget.parentWidget()
    while parent is not None:
        if parent is owner:
            return True
        parent = parent.parentWidget()
    return False


def prompt_operation_settings(parent, *, title: str, feature: str, detail: str, navigate, target: str) -> None:
    """Only an explicit navigation click leaves the current feature dialog."""
    origin = QApplication.activeModalWidget()
    dialog = QDialog(parent)
    dialog.setWindowTitle(title)
    layout = QVBoxLayout(dialog)
    text = QLabel(
        feature + "\n\n" + detail + "\n\n设置完成后，请返回并重新点击此功能；本次操作不会自动继续。",
        dialog,
    )
    text.setTextFormat(Qt.PlainText)
    text.setWordWrap(True)
    layout.addWidget(text)
    buttons = QDialogButtonBox(QDialogButtonBox.Cancel, parent=dialog)
    cancel = buttons.button(QDialogButtonBox.Cancel)
    cancel.setText("取消")
    go = buttons.addButton("前往设置", QDialogButtonBox.AcceptRole)
    go.setAutoDefault(False)
    cancel.setDefault(True)
    cancel.setFocus()
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    layout.addWidget(buttons)
    fit_dialog_to_available_screen(dialog, QSize(600, 245))
    if dialog.exec() != QDialog.Accepted:
        return
    if isinstance(origin, QDialog) and origin is not parent and _belongs_to(origin, parent):
        origin.reject()
    navigate(target)


def allow_operation_entry(parent, policy, capability: str, feature: str, navigate) -> bool:
    if entry_is_allowed(policy, capability):
        return True
    mode = _MODE_LABELS[policy.settings.mode.value]
    detail = "当前" + mode + "模式不允许此功能。\n" + _REQUIREMENTS[capability]
    prompt_operation_settings(parent, title="功能受限", feature=feature, detail=detail,
                              navigate=navigate, target="mode")
    return False


def explain_operation_unavailable(parent, feature: str, detail: str, navigate, target: str = "detection") -> None:
    target = "deployment" if target == "deployment" else "detection"
    destination = "组件部署设置" if target == "deployment" else "当前检测信息；需要时请点击重新检测"
    prompt_operation_settings(
        parent, title="功能暂不可用", feature=feature,
        detail=detail + "\n可前往设置查看" + destination + "。",
        navigate=navigate, target=target,
    )
