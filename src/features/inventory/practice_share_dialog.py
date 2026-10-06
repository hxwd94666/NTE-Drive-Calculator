# 提供游戏配装的练度统计入口，复用可取消且绑定账号代次的图片预览。
from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from src.domain.work_mode import WorkMode
from src.features.inventory.loadout_share_dialog import LoadoutShareDialog
from src.integrations.practice_share_image import render_practice_share_png
from src.services.practice_share_service import PracticeShareRequest, build_practice_share_panel


def _generate(request, *, cancelled):
    panel = build_practice_share_panel(request, cancelled=cancelled)
    data = render_practice_share_png(panel, cancelled=cancelled)
    return data, '\n'.join(panel.notes)


def open_practice_share(owner) -> None:
    if getattr(owner, '_equipment_mode', 'saved') != 'game':
        return
    context = owner.app_context
    states = deepcopy(tuple((getattr(owner, '_game_loadout_states', {}) or {}).values()))
    policy = getattr(owner, 'work_mode_service', None)
    mode = policy.settings.mode if policy is not None else WorkMode.OFFLINE
    request = PracticeShareRequest(
        user_database_path=Path(context.account.user_database_path),
        static_database_path=Path(context.paths.static_database_path),
        asset_root=Path(context.paths.game_ui_asset_root), states=states, work_mode=mode,
    )
    dialog = LoadoutShareDialog(
        request, owner=owner, generation=context.generation, title='练度统计',
        filename='练度统计-NTE DRIVE CALC', generate=_generate,
        ready_message=('练度统计已生成；账号角色含未装备角色，全未知占位已排除；＊为配置，—为缺失。'
                       if mode in {WorkMode.MEDIUM, WorkMode.DEVELOPER} else
                       '练度统计已生成；仅含已知有游戏配装的角色；＊为账号配置或模板，—为缺失。'),
    )
    try:
        dialog.exec()
    finally:
        dialog.deleteLater()
