# 使用临时合成组件验证整套部署、撤权待清理和失败回滚。
from dataclasses import asdict
import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from src.services import native_plugin_deployment as module
from src.services.equipment_plugin_deployment import EquipmentPluginDeploymentError, PluginDeploymentPendingCleanup


class NativePluginDeploymentTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.package = self.root / 'package'
        self.game = self.root / 'game'
        self.package.mkdir()
        self.game.mkdir()
        self.executable = self.game / 'HTGame.exe'
        self.executable.write_bytes(b'fake game')
        self.roles, self.hashes = {}, {}
        for role in module.NATIVE_PLUGIN_DEPLOYMENT_PATHS:
            relative = role + '.dll'
            self.roles[role] = relative
            data = ('new-' + role).encode()
            (self.package / relative).write_bytes(data)
            self.hashes[relative] = hashlib.sha256(data).hexdigest()
        self.bundle = SimpleNamespace(ready=True, issues=(), roles=self.roles, files=self.hashes)
        self.inspection = patch.object(module, 'inspect_native_plugin_bundle', return_value=self.bundle)
        self.inspection.start()
        self.addCleanup(self.inspection.stop)
        self.addCleanup(self.temporary.cleanup)
        self.allowed = True
        self.running = False

    def guard(self, _capability):
        if not self.allowed:
            raise PermissionError('revoked')

    def deploy(self):
        return module.deploy_native_plugin(
            application_root=self.package, game_executable_path=self.executable,
            operation_guard=self.guard,
            game_running=lambda: self.running,
        )

    def cleanup(self, files):
        return module.cleanup_native_plugin(
            game_executable_path=self.executable, managed_files=files,
            game_running=lambda: self.running,
        )

    def populate_old(self):
        for role, relative in module.NATIVE_PLUGIN_DEPLOYMENT_PATHS.items():
            target = self.game / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(('old-' + role).encode())

    def assert_old(self):
        for role, relative in module.NATIVE_PLUGIN_DEPLOYMENT_PATHS.items():
            self.assertEqual(('old-' + role).encode(), (self.game / relative).read_bytes())

    def test_deploys_fixed_two_files_host_last_without_backups(self):
        self.populate_old()
        actual_replace = module.os.replace
        order = []
        def replace(source, target):
            order.append(Path(target).relative_to(self.game).as_posix())
            actual_replace(source, target)
        with patch.object(module.os, 'replace', side_effect=replace):
            result = self.deploy()
        self.assertEqual([
            module.NATIVE_PLUGIN_DEPLOYMENT_PATHS[role] for role in ('capture_plugin', 'host')
        ], order)
        self.assertEqual(set(order), set(result.managed_files))
        self.assertEqual('native-capture', asdict(result)['loading_method'])
        self.assertIsNone(result.backup_path)
        self.assertFalse((self.root/'backups').exists())
        self.assertEqual(self.game / 'd3d12.dll', result.target_path)
        self.assertEqual(self.game, result.workspace_path)
        self.assertEqual('native-capture-v1', result.deployment_layout)

    def test_replaces_use_target_directory_and_failure_does_not_restore_old_files(self):
        self.populate_old()
        actual_replace = module.os.replace
        replacements = []
        def same_directory_replace(source, target):
            source, target = Path(source), Path(target)
            self.assertEqual(target.parent, source.parent)
            replacements.append(source.suffix)
            if target.name == 'd3d12.dll' and source.suffix == '.new':
                raise OSError('synthetic later-file failure')
            actual_replace(source, target)
        with patch.object(module.os, 'replace', side_effect=same_directory_replace):
            with self.assertRaises(EquipmentPluginDeploymentError):
                self.deploy()
        self.assertNotIn('.rollback', replacements)
        self.assertFalse((self.game/'NTE_Capture.dll').exists())
        self.assertEqual(b'old-host', (self.game/'d3d12.dll').read_bytes())
        self.assertEqual([], list(self.game.rglob('.nte-deploy-*')))
        def successful_replace(source, target):
            self.assertEqual(Path(target).parent, Path(source).parent)
            actual_replace(source, target)
        with patch.object(module.os, 'replace', side_effect=successful_replace):
            self.assertEqual(2, len(self.deploy().managed_files))
        self.assertEqual([], list(self.game.rglob('.nte-deploy-*')))

    def test_missing_or_changed_package_has_zero_game_writes(self):
        self.bundle.ready, self.bundle.issues = False, ('缺少整套组件',)
        with self.assertRaises(EquipmentPluginDeploymentError):
            self.deploy()
        self.bundle.ready = True
        (self.package / self.roles['host']).write_bytes(b'changed')
        with self.assertRaises(EquipmentPluginDeploymentError):
            self.deploy()
        self.assertEqual(['HTGame.exe'], sorted(path.name for path in self.game.iterdir()))

    def test_game_running_or_missing_guard_has_zero_writes(self):
        self.running = True
        with self.assertRaises(EquipmentPluginDeploymentError):
            self.deploy()
        self.running = False
        with self.assertRaises(PermissionError):
            module.deploy_native_plugin(application_root=self.package,
                game_executable_path=self.executable,
                operation_guard=None, game_running=lambda: False)
        self.assertFalse((self.root / 'backups').exists())

    def test_write_failure_removes_new_files_and_retry_succeeds(self):
        self.populate_old()
        actual_replace = module.os.replace
        def fail_host(source, target):
            if Path(target).name == 'd3d12.dll' and str(source).endswith('.new'):
                raise OSError('synthetic write failure')
            actual_replace(source, target)
        with patch.object(module.os, 'replace', side_effect=fail_host):
            with self.assertRaises(EquipmentPluginDeploymentError):
                self.deploy()
        self.assertFalse((self.game/'NTE_Capture.dll').exists())
        self.assertEqual(b'old-host', (self.game/'d3d12.dll').read_bytes())
        result = self.deploy()
        self.assertEqual(2, len(result.managed_files))

    def test_failure_removes_only_newly_created_files(self):
        actual_replace = module.os.replace
        def fail_host(source, target):
            if Path(target).name == 'd3d12.dll':
                raise OSError('synthetic host failure')
            actual_replace(source, target)
        with patch.object(module.os, 'replace', side_effect=fail_host):
            with self.assertRaises(EquipmentPluginDeploymentError):
                self.deploy()
        for relative in module.NATIVE_PLUGIN_DEPLOYMENT_PATHS.values():
            self.assertFalse((self.game / relative).exists())

    def test_revocation_or_game_start_keeps_only_written_pending_record(self):
        for revoke in (True, False):
            with self.subTest(revoke=revoke):
                self.populate_old()
                self.allowed, self.running = True, False
                actual_replace = module.os.replace
                def revoke_after_first(source, target):
                    actual_replace(source, target)
                    if revoke:
                        self.allowed = False
                    else:
                        self.running = True
                with patch.object(module.os, 'replace', side_effect=revoke_after_first):
                    with self.assertRaises(PluginDeploymentPendingCleanup) as error:
                        self.deploy()
                record = error.exception.deployment
                self.assertEqual({module.NATIVE_PLUGIN_DEPLOYMENT_PATHS['capture_plugin']}, set(record.managed_files))
                self.assertEqual(b'old-host', (self.game/'d3d12.dll').read_bytes())
                self.running = False
                self.assertEqual('cleaned', self.cleanup(record.managed_files).status)

    def test_cleanup_only_recorded_files_and_never_restores_old_backups(self):
        self.populate_old()
        result = self.deploy()
        unrelated = self.game/'dwmapi.dll'
        unrelated.write_bytes(b'other tool')
        self.allowed = False
        self.assertEqual('cleaned', self.cleanup(result.managed_files).status)
        self.assertEqual(b'other tool', unrelated.read_bytes())
        self.assertIsNone(result.backup_path)
        self.assertFalse((self.root/'backups').exists())
        for relative in result.managed_files:
            self.assertFalse((self.game/relative).exists())
        self.assertEqual('cleaned', self.cleanup(result.managed_files).status)

    def test_cleanup_waits_and_rejects_unlisted_paths_but_removes_upgraded_files(self):
        result = self.deploy()
        self.running = True
        self.assertEqual('waiting_game_exit', self.cleanup(result.managed_files).status)
        self.running = False
        self.assertEqual('conflict', self.cleanup({'../outside.dll': 'wrong'}).status)
        target = self.game/'NTE_Capture.dll'
        target.write_bytes(b'user change')
        with patch.object(module, '_digest', side_effect=AssertionError('cleanup must not hash')):
            self.assertEqual('cleaned', self.cleanup(result.managed_files).status)
        self.assertFalse((self.game/'d3d12.dll').exists())
        self.assertFalse(target.exists())


    def test_target_changed_during_final_copy_is_preserved(self):
        self.populate_old()
        actual_copy = module.shutil.copy2
        target = self.game / module.NATIVE_PLUGIN_DEPLOYMENT_PATHS['capture_plugin']
        def copy(source, destination, *args, **kwargs):
            value = actual_copy(source, destination, *args, **kwargs)
            path = Path(destination)
            if path.parent == target.parent and path.suffix == '.new':
                target.write_bytes(b'new external contents')
            return value
        with patch.object(module.shutil, 'copy2', side_effect=copy):
            with self.assertRaisesRegex(EquipmentPluginDeploymentError, '暂存期间发生变化'):
                self.deploy()
        self.assertEqual(b'new external contents', target.read_bytes())
        self.assertEqual(b'old-host', (self.game / 'd3d12.dll').read_bytes())
        self.assertEqual([], list(self.game.rglob('.nte-deploy-*')))

    def test_changed_new_file_on_failure_keeps_pending_cleanup_record(self):
        self.populate_old()
        actual_replace = module.os.replace
        target = self.game / 'NTE_Capture.dll'
        def replace(source, destination):
            if Path(destination).name == 'd3d12.dll':
                target.write_bytes(b'external change')
                raise OSError('later target failed')
            return actual_replace(source, destination)
        with patch.object(module.os, 'replace', side_effect=replace):
            with self.assertRaises(PluginDeploymentPendingCleanup) as failure:
                self.deploy()
        self.assertEqual(b'external change', target.read_bytes())
        self.assertIn('NTE_Capture.dll', failure.exception.deployment.managed_files)
        self.assertIsNone(failure.exception.deployment.backup_path)
        self.assertEqual([], list(self.game.rglob('.nte-deploy-*')))

    def test_capture_only_selection_never_writes_or_cleans_host(self):
        self.populate_old()
        unrelated = self.game / 'NTE-Platform.dll'
        unrelated.write_bytes(b'other tool platform')
        result = module.deploy_native_component_files(
            application_root=self.package, directory_path=self.game,
            operation_guard=self.guard,
            game_running=lambda: False, component_roles=('capture_plugin',),
        )
        self.assertEqual({'NTE_Capture.dll'}, set(result.managed_files))
        self.assertEqual(b'old-host', (self.game/'d3d12.dll').read_bytes())
        self.assertIsNone(result.backup_path)
        self.assertEqual('cleaned', module.cleanup_native_component_files(
            directory_path=self.game, managed_files=result.managed_files,
            game_running=lambda: False,
        ).status)
        self.assertEqual(b'old-host', (self.game/'d3d12.dll').read_bytes())
        self.assertEqual(b'other tool platform', unrelated.read_bytes())
        self.assertFalse((self.game/'NTE_Capture.dll').exists())

    def test_capture_only_revocation_preserves_shared_pending_record(self):
        self.populate_old()
        actual_replace = module.os.replace
        def replace(source, target):
            actual_replace(source, target)
            self.allowed = False
        with patch.object(module.os, 'replace', side_effect=replace):
            with self.assertRaises(module.NativeComponentFilesPendingCleanup) as failure:
                module.deploy_native_component_files(
                    application_root=self.package, directory_path=self.game,
                    operation_guard=self.guard,
                    game_running=lambda: False, component_roles=('capture_plugin',),
                )
        self.assertEqual({'NTE_Capture.dll'}, set(failure.exception.deployment.managed_files))
        self.assertEqual(b'old-host', (self.game/'d3d12.dll').read_bytes())
        self.assertEqual([], list(self.game.rglob('.nte-deploy-*')))

    def test_selected_roles_still_put_host_last(self):
        actual_replace = module.os.replace
        order = []
        def replace(source, target):
            order.append(Path(target).name)
            actual_replace(source, target)
        with patch.object(module.os, 'replace', side_effect=replace):
            module.deploy_native_component_files(
                application_root=self.package, directory_path=self.game,
                operation_guard=self.guard,
                game_running=lambda: False, component_roles=('host', 'capture_plugin'),
            )
        self.assertEqual(['NTE_Capture.dll', 'd3d12.dll'], order)

    def test_removed_layout_roles_are_not_deployed_or_cleaned(self):
        with self.assertRaises(EquipmentPluginDeploymentError):
            module.deploy_native_component_files(
                application_root=self.package, directory_path=self.game,
                operation_guard=self.guard,
                game_running=lambda: False, component_roles=('platform',),
            )
        self.assertFalse((self.root/'backups').exists())
        previous = self.game/'NTE-Platform.dll'
        previous.write_bytes(b'other tool')
        self.assertEqual('conflict', self.cleanup({
            'NTE-Platform.dll': hashlib.sha256(b'other tool').hexdigest(),
        }).status)
        self.assertEqual(b'other tool', previous.read_bytes())
