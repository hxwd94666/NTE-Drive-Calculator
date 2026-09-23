# 验证角色同步入口、完整读取后提交和取消账号代次边界。
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from PySide6.QtWidgets import QApplication, QPlainTextEdit, QPushButton, QWidget

from src.domain.native_role_sync import NativeRoleSyncResult
from src.features.official_role.dependencies import OfficialRoleDependencies
from src.features.official_role.sync_controller import CharacterProfileSyncController


class DeferredThread:
    def __init__(self, *, target, **_kwargs):
        self.target = target
    def start(self):
        pass


class CharacterProfileSyncControllerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.owner = QWidget()
        self.generation = 1
        self.dependencies = OfficialRoleDependencies('a', 1, Path('a.db'), Path('s.db'), Path('shared.db'))
        self.reader = Mock(return_value={'profiles': [{'character_id': 101, 'character_level': 20}]})
        self.service = Mock()
        self.service.patch_native_profiles.return_value = NativeRoleSyncResult(1)
        self.entry = Mock(return_value=True)
        self.unavailable = Mock()
        self.sync_ready = Mock(return_value=True)
        self.hotkeys = SimpleNamespace(active_owner=None, start=Mock(), stop=Mock())
        self.refresh = Mock()
        self.controller = CharacterProfileSyncController(
            parent=self.owner, dependencies_factory=lambda: self.dependencies,
            operation_generation=lambda: self.generation, read_profiles=self.reader,
            operation_guard=lambda _cap: None, operation_entry=self.entry,
            operation_unavailable=self.unavailable, hotkey_manager=self.hotkeys,
            refresh=self.refresh,
            sync_ready=self.sync_ready,
            profile_service_factory=lambda _path, **_kwargs: self.service, thread_factory=DeferredThread,
        )
        self.button, self.editor = QPushButton(), QWidget()
        self.result_text = QPlainTextEdit(self.owner)
        self.controller.attach_controls(self.button, (self.editor,), result_text=self.result_text)

    def tearDown(self):
        self.controller.close()
        self.owner.close()

    def finish_read(self):
        self.controller._thread.target()

    def test_real_role_page_runs_independent_sync(self):
        from src.features.official_role import role_shell
        from src.services.world_bonus_settings_service import WorldBonusSettings
        window = SimpleNamespace(character_profile_sync_controller=self.controller)
        role_controller = SimpleNamespace(
            dependencies=self.dependencies,
            load_world_bonus=lambda: WorldBonusSettings(),
        )
        with patch.object(role_shell, '_role_controller', return_value=role_controller), patch.object(role_shell, '_refresh_my_role'):
            page = role_shell._page_my_role(window)
        button = next(
            item for item in page.findChildren(QPushButton)
            if item.text() == '同步状态'
        )
        self.assertIsNotNone(button)
        button.click()
        self.assertFalse(window.my_role_form_area.isEnabled())
        self.finish_read()
        self.assertTrue(window.my_role_form_area.isEnabled())
        self.service.patch_native_profiles.assert_called_once()
        page.close()

    def test_paused_connection_guides_before_worker(self):
        self.controller._connection_paused = lambda: True
        self.controller.start()
        self.reader.assert_not_called()
        self.assertFalse(self.controller.is_running())
        self.unavailable.assert_called_once()
        self.assertEqual('detection', self.unavailable.call_args.args[2])

    def test_inactive_data_sync_guides_to_workbench_before_worker(self):
        self.sync_ready.return_value = False
        self.controller.start()
        self.reader.assert_not_called()
        self.assertFalse(self.controller.is_running())
        self.unavailable.assert_called_once_with(
            '同步状态',
            '当前没有正在运行的游戏数据同步，程序暂时无法读取角色状态。\n\n'
            '请先：\n'
            '1. 在工作台开启“自动同步”；\n'
            '2. 登录并进入游戏场景；\n'
            '3. 等待工作台显示“同步中”，再返回点击“同步状态”。',
            'home',
        )
        self.entry.assert_called_once_with('native_sync', '同步状态')

    def test_component_capability_errors_target_deployment(self):
        for code in ('NATIVE_CAPABILITY_MISSING', 'NATIVE_MAPPING_UNSUPPORTED'):
            with self.subTest(code=code):
                error = RuntimeError('当前组件不支持已证实角色字段')
                error.domain_code = code
                self.reader.side_effect = error
                self.controller.start()
                self.finish_read()
                self.assertEqual('deployment', self.unavailable.call_args.args[2])
        self.service.patch_native_profiles.assert_not_called()

    def test_inventory_cancel_is_silent(self):
        from src.services.inventory_capture_wait import InventorySyncCancelled
        self.reader.side_effect = InventorySyncCancelled()
        self.controller.start()
        self.finish_read()
        self.unavailable.assert_not_called()
        self.service.patch_native_profiles.assert_not_called()

    def test_business_save_failure_does_not_guide_component_detection(self):
        self.service.patch_native_profiles.side_effect = ValueError('角色成长组合无效')
        with patch('src.features.official_role.sync_controller.QMessageBox.warning') as warning:
            self.controller.start()
            self.finish_read()
        warning.assert_called_once_with(self.owner, '角色状态未保存', '角色成长组合无效')
        self.unavailable.assert_not_called()
        self.refresh.assert_not_called()

    def test_partial_success_displays_all_warnings_without_modal_dialog(self):
        result = NativeRoleSyncResult(2, ('角色 101 · 弧盘 fork_unknown：不在当前官方目录中',
                                         '角色 102 · 技能 unknown_skill：不在当前官方目录中'))
        self.service.patch_native_profiles.return_value = result
        with patch('src.features.official_role.sync_controller.QMessageBox.warning') as warning, patch(
            'src.features.official_role.sync_controller.QMessageBox.information'
        ) as information:
            self.controller.start()
            self.finish_read()
        self.refresh.assert_called_once()
        self.assertEqual(self.result_text.toPlainText(), result.message)
        self.assertIn('fork_unknown', self.button.toolTip())
        warning.assert_not_called()
        information.assert_not_called()
        self.controller.request_stop()
        self.assertEqual(self.result_text.toPlainText(), '')

    def test_no_valid_fields_keeps_drafts_and_reports_no_write(self):
        self.service.patch_native_profiles.return_value = NativeRoleSyncResult(0, ('弧盘身份未知',))
        self.controller.start()
        self.finish_read()
        self.refresh.assert_not_called()
        self.assertIn('未写入新的角色状态', self.result_text.toPlainText())

    def test_mode_decline_precedes_worker_and_ui_mutation(self):
        self.entry.return_value = False
        self.controller.start()
        self.entry.assert_called_once_with('native_sync', '同步状态')
        self.assertFalse(self.controller.is_running())
        self.assertTrue(self.editor.isEnabled())
        self.reader.assert_not_called()
        self.hotkeys.start.assert_not_called()

    def test_only_complete_profiles_are_patched_then_refreshed(self):
        self.controller.start()
        self.assertFalse(self.editor.isEnabled())
        self.assertEqual('取消同步', self.button.text())
        self.service.patch_native_profiles.assert_not_called()
        self.finish_read()
        self.service.patch_native_profiles.assert_called_once()
        self.assertEqual(self.reader.return_value['profiles'], self.service.patch_native_profiles.call_args.args[0])
        self.refresh.assert_called_once()
        self.assertTrue(self.editor.isEnabled())
        self.assertEqual('同步状态', self.button.text())

    def test_cancel_during_read_never_patches_or_shows_error(self):
        def read(*, check):
            self.controller.request_stop()
            check()
        self.reader.side_effect = read
        self.controller.start()
        self.finish_read()
        self.service.patch_native_profiles.assert_not_called()
        self.unavailable.assert_not_called()
        self.assertFalse(self.controller.is_running())

    def test_new_account_or_mode_discards_old_read(self):
        for change in ('account', 'mode'):
            with self.subTest(change=change):
                self.controller.start()
                def read(*, check):
                    if change == 'account':
                        self.dependencies = OfficialRoleDependencies('b', 2, Path('b.db'), Path('s.db'), Path('shared.db'))
                    else:
                        self.generation += 1
                    return {'profiles': [{'character_id': 101, 'character_level': 30}]}
                self.reader.side_effect = read
                self.finish_read()
                self.service.patch_native_profiles.assert_not_called()
                self.refresh.assert_not_called()
                self.unavailable.assert_not_called()

    def test_read_failure_explains_connection_and_does_not_commit(self):
        self.reader.side_effect = RuntimeError('原生组件不支持角色快照')
        self.controller.start()
        self.finish_read()
        self.service.patch_native_profiles.assert_not_called()
        self.unavailable.assert_called_once_with('同步状态', '原生组件不支持角色快照', 'detection')

    def test_close_and_hotkey_cancel_keep_final_results_from_writing(self):
        self.controller.start()
        self.hotkeys.start.call_args.kwargs['on_stop']()
        self.finish_read()
        self.controller.start()
        self.controller.close()
        self.finish_read()
        self.service.patch_native_profiles.assert_not_called()
        self.refresh.assert_not_called()

    def test_second_click_cancels_without_new_mode_prompt(self):
        self.button.click()
        self.button.click()
        self.finish_read()
        self.entry.assert_called_once()
        self.reader.assert_not_called()
        self.service.patch_native_profiles.assert_not_called()

    def _pending_drafts(self):
        return SimpleNamespace(
            _official_role_dirty_ids={101, 999}, _official_role_world_bonus_dirty=True,
            _my_role_dirty=True, _official_role_editors={101: {'draft': 'manual'}},
        )

    def test_success_replaces_pending_edits_only_after_persist(self):
        from src.features.official_role import page
        drafts = self._pending_drafts()
        events = []
        def persist(_profiles, *, check):
            check()
            self.assertTrue(drafts._my_role_dirty)
            self.assertEqual({101, 999}, drafts._official_role_dirty_ids)
            events.append('persist')
            return NativeRoleSyncResult(1)
        self.service.patch_native_profiles.side_effect = persist
        self.controller._refresh = lambda: page.refresh_official_role_page(drafts, discard_pending=True)
        factory = Mock(return_value=self.service)
        self.controller._service_factory = factory
        with patch.object(page, '_refresh_my_role', side_effect=lambda *_a, **_kw: events.append('refresh')), patch('src.features.official_role.sync_controller.QMessageBox.information') as prompt:
            self.controller.start()
            self.assertTrue(drafts._my_role_dirty)
            self.finish_read()
        prompt.assert_not_called()
        self.assertEqual(['persist', 'refresh'], events)
        self.assertFalse(drafts._my_role_dirty)
        self.assertFalse(drafts._official_role_world_bonus_dirty)
        self.assertEqual(set(), drafts._official_role_dirty_ids)
        self.assertEqual({}, drafts._official_role_editors)
        factory.assert_called_once_with(Path('a.db'), static_database_path=Path('s.db'))

    def test_cancel_and_failures_preserve_all_pending_drafts(self):
        from src.features.official_role import page
        for outcome in ('cancel', 'read_failure', 'save_failure'):
            with self.subTest(outcome=outcome):
                drafts = self._pending_drafts()
                self.controller._refresh = lambda: page.refresh_official_role_page(drafts, discard_pending=True)
                self.reader.side_effect = RuntimeError('连接失败') if outcome == 'read_failure' else None
                self.service.patch_native_profiles.side_effect = ValueError('成长无效') if outcome == 'save_failure' else None
                with patch.object(page, '_refresh_my_role') as refresh, patch('src.features.official_role.sync_controller.QMessageBox.warning'):
                    self.controller.start()
                    if outcome == 'cancel':
                        self.controller.request_stop()
                    self.finish_read()
                refresh.assert_not_called()
                self.assertTrue(drafts._my_role_dirty)
                self.assertTrue(drafts._official_role_world_bonus_dirty)
                self.assertEqual({101, 999}, drafts._official_role_dirty_ids)
                self.assertEqual({101: {'draft': 'manual'}}, drafts._official_role_editors)

    def test_normal_public_refresh_does_not_discard_pending_flags(self):
        from src.features.official_role import page
        drafts = self._pending_drafts()
        with patch.object(page, '_refresh_my_role'):
            page.refresh_official_role_page(drafts)
        self.assertTrue(drafts._my_role_dirty)
        self.assertTrue(drafts._official_role_world_bonus_dirty)
        self.assertEqual({101, 999}, drafts._official_role_dirty_ids)
