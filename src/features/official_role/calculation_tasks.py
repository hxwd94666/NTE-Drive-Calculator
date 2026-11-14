# 角色编辑刷新复用Controller读取任务；只把最新冻结数值应用到仍存活的页面。
from __future__ import annotations

from shiboken6 import isValid

from src.domain.catalog.static_catalog import StaticCatalogRelease

from src.features.official_role.controller import OfficialRoleController
from src.features.official_role.role_calculation import _calculation_detail, _role_refresh_signature, _refresh_role_calculations
from src.ui.common.text_catalog import plain_label, resolve_text_catalog


def bind_calculation_updates(window, scroll, content, editor):
    controller = getattr(window, '_official_role_controller', None)
    if not isinstance(controller, OfficialRoleController):
        return
    detail = editor['detail']
    if (not isinstance(detail.get('static_release'), StaticCatalogRelease)
            or detail.get('catalog_scope') in {'role_page', 'reference'}):
        return
    state = {'signature': _role_refresh_signature(editor), 'request': None}
    status = plain_label(content)
    status.setObjectName('officialRoleCalculationState')
    status.setWordWrap(True)
    status.hide()
    content.layout().insertWidget(0, status)
    catalog = resolve_text_catalog(getattr(window, 'text_catalog', None))
    groups = [group for group in content.findChildren(type(content))
              if group.objectName() in {'officialRoleMarginalGroup', 'officialRoleDamageFormulaGroup', 'officialRoleDriveGroup'}]

    def alive(token):
        return (isValid(scroll) and isValid(content) and getattr(window, '_official_role_controller', None) is controller
                and not controller._closed and state['request'] is token)

    def request():
        signature = _role_refresh_signature(editor)
        if signature == state['signature'] and state['request'] is None:
            for group in groups:
                group.setEnabled(True)
            status.clear()
            status.hide()
            return
        token = object()
        state['request'] = token
        editor['calculation_pending'] = True
        status.setText(catalog.render('roles.page.calculation.pending'))
        status.show()
        for group in groups:
            group.setEnabled(False)
        calculation_detail = _calculation_detail(detail, editor)
        context_key = str(editor.get('equipment_context_key') or 'current')

        def done(values):
            if not alive(token):
                return
            # 值只属于这份草稿；编辑再次变化时旧回执不应用，也不改角色档案或配装。
            if _role_refresh_signature(editor) != signature:
                request()
                return
            detail['_role_damage_values'] = values
            editor['applying_calculation_values'] = True
            try:
                _refresh_role_calculations(editor)
            finally:
                editor.pop('applying_calculation_values', None)
            state['signature'] = _role_refresh_signature(editor)
            state['request'] = None
            editor['calculation_pending'] = False
            for group in groups:
                group.setEnabled(True)
            status.clear()
            status.hide()

        def failed(error):
            if alive(token):
                state['request'] = None
                state['signature'] = None
                editor['calculation_pending'] = False
                status.setText(catalog.render('roles.page.calculation.failed', error=error))
                # 旧结果保持禁用，避免把过期数值误看作当前配置已完成；下一次编辑可重试。
                status.show()

        def discarded():
            if alive(token):
                state['request'] = None
                editor['calculation_pending'] = False
                status.setText(catalog.render('roles.page.calculation.cancelled'))
                status.show()

        controller.request_calculation(calculation_detail, context_key,
            dict(detail.get('property_weights') or {}), dict(detail.get('main_property_weights') or {}),
            done, failed, discarded)

    editor['request_calculation'] = request
