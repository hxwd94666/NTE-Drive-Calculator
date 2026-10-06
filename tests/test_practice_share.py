# 覆盖练度导出的冻结评分、缺失养成、正式技能顺序及账号读取边界。
from __future__ import annotations

import unittest
from concurrent.futures import CancelledError
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from src.domain.practice_share import PracticeEntry, PracticeSharePanel, PracticeValue, sort_practice_entries
from src.integrations.practice_share_image import render_practice_share_png
from src.services.practice_share_service import PracticeShareRequest, build_practice_share_panel
from src.storage.sqlite.user_data_dao import UserDataDao


class PracticeShareTests(unittest.TestCase):
    def test_missing_scores_are_last_and_known_zero_is_not_missing(self):
        missing = PracticeEntry(1, '未知')
        zero = PracticeEntry(2, '零分', score=0, grade='D')
        high = PracticeEntry(3, '高分', score=301.3, grade='ACE')
        self.assertEqual(sort_practice_entries((missing, zero, high)), (high, zero, missing))

    def test_only_current_equipped_projections_define_statistics_roster(self):
        from types import SimpleNamespace
        from src.services.practice_share_service import _equipped_character_ids
        states = {5: {'_game_projection': SimpleNamespace(items=({'kind': 'module'},))},
                  6: {'_game_projection': SimpleNamespace(items=())}, 7: {},
                  8: {'_game_projection': SimpleNamespace(items=({'kind': 'core'},))},
                  9: {'_game_projection': SimpleNamespace(items=({'kind': 'unknown'},))}}
        self.assertEqual(_equipped_character_ids(states), {5, 8})

    def test_roster_is_mode_specific_and_empty_native_records_do_not_prove_ownership(self):
        from types import SimpleNamespace
        from src.domain.work_mode import WorkMode
        from src.services.practice_share_service import _practice_character_ids
        states = {1: {'_game_projection': SimpleNamespace(items=({'kind': 'module'},))}}
        context = {'profiles': [{'character_id': 4, 'character_level': 80},
                                {'character_id': 5, 'character_level': 60}],
                   'observed_ids': [3, 4, 5],
                   'observations': [{'character_id': 2, 'character_level': 50},
                                    {'character_id': 4, 'character_level': None, 'skill_levels': {}, 'fork_id': None},
                                    {'character_id': 6, 'likeability_level': 0}]}
        for mode in (WorkMode.LOW, WorkMode.OFFLINE):
            self.assertEqual(_practice_character_ids(mode, context, states), {1})
        for mode in (WorkMode.MEDIUM, WorkMode.DEVELOPER):
            self.assertEqual(_practice_character_ids(mode, context, states), {1, 2, 5, 6})

    def test_unknown_display_row_is_dropped_but_known_zero_is_retained(self):
        from src.services.practice_share_service import _entry_has_known_data, _has_known_cultivation
        self.assertFalse(_has_known_cultivation({'character_id': 1, 'updated_at_utc': 'fixture',
                                               'character_level': None, 'skill_levels': {},
                                               'fork_observed': True, 'fork_id': None}))
        self.assertFalse(_entry_has_known_data(PracticeEntry(1, '角色', active_set_count=0)))
        self.assertTrue(_entry_has_known_data(PracticeEntry(1, '角色', awakening=PracticeValue('0'))))
        self.assertTrue(_entry_has_known_data(PracticeEntry(1, '角色', heart=PracticeValue('0'))))
        self.assertTrue(_entry_has_known_data(PracticeEntry(1, '角色', score=0)))

    def test_entry_point_freezes_confirmed_work_mode_in_export_request(self):
        from types import SimpleNamespace
        from src.domain.work_mode import WorkMode
        from src.features.inventory.practice_share_dialog import open_practice_share
        owner = SimpleNamespace(_equipment_mode='game', _game_loadout_states={},
            work_mode_service=SimpleNamespace(settings=SimpleNamespace(mode=WorkMode.MEDIUM)),
            app_context=SimpleNamespace(generation=1, account=SimpleNamespace(user_database_path=Path('unused')),
                paths=SimpleNamespace(static_database_path=Path('unused'), game_ui_asset_root=Path('unused'))))
        with patch('src.features.inventory.practice_share_dialog.LoadoutShareDialog') as dialog:
            open_practice_share(owner)
            request = dialog.call_args.args[0]
            owner.work_mode_service.settings.mode = WorkMode.LOW
            self.assertEqual(request.work_mode, WorkMode.MEDIUM)
            self.assertIn('未装备', dialog.call_args.kwargs['ready_message'])

    def test_resolved_role_page_fields_preserve_sources_and_skill_order(self):
        from src.services.practice_share_service import _profile_value, _skill_values
        profile = {'character_level': 70, 'awakening_level': 2,
                   'skill_levels': {'GA_Role_Melee': 6, 'GA_Role_Skill': 8},
                   'field_sources': {'character_level': 'native_observed', 'awakening_level': 'account',
                                     'skill_levels': 'account'}}
        self.assertEqual(_profile_value(profile, 'character_level'), PracticeValue('70'))
        self.assertEqual(_profile_value(profile, 'awakening_level'), PracticeValue('2', True))
        skills = [{'skill_id': 'GA_Role_Skill', 'ability_index': 2, 'ability_type': 'Proactive'},
                  {'skill_id': 'GA_Role_Melee', 'ability_index': 1, 'ability_type': 'Proactive'}]
        self.assertEqual(_skill_values(profile, skills),
                         (PracticeValue('6', True), PracticeValue('8', True), PracticeValue(), PracticeValue()))

    def test_shared_role_profile_uses_saved_account_else_template(self):
        from src.services.official_role_profile_projection import resolve_official_role_profile
        args = ({'character_id': 1}, [{'level': 80, 'breakthrough_stage': 6}], [], [], [])
        template = resolve_official_role_profile(*args, saved_profile=None, observation=None, likeability_bonus=None)
        saved = {**template, 'character_level': 60, 'breakthrough_stage': 4, 'awakening_level': 2}
        configured = resolve_official_role_profile(*args, saved_profile=saved, observation=None, likeability_bonus=None)
        self.assertEqual((template['character_level'], template['field_sources']['character_level']), (80, 'template'))
        self.assertEqual((configured['character_level'], configured['field_sources']['character_level']), (60, 'account'))
        self.assertEqual(configured['awakening_level'], 2)

    def test_heart_displays_only_known_exact_levels(self):
        from src.services.practice_share_service import _heart_value
        self.assertEqual(_heart_value({}), PracticeValue())
        self.assertEqual(_heart_value({'likeability_level_10_enabled': True}), PracticeValue())
        self.assertEqual(_heart_value({'likeability_level_10_enabled': False}), PracticeValue())
        for exact in (0, 7, 10):
            self.assertEqual(_heart_value({'likeability_level': exact, 'field_sources': {'likeability_level': 'native_observed'}}),
                             PracticeValue(str(exact)))
        self.assertEqual(_heart_value({'likeability_level': 4}), PracticeValue('4', True))
        self.assertEqual(_heart_value({'likeability_level': None}), PracticeValue())
        self.assertEqual(_heart_value({'likeability_level': -1}), PracticeValue())

    def test_suit_activation_counts_distinct_shapes_not_drive_quantity(self):
        from src.domain.loadout_suit_activation import active_set_count
        suit = {'required_shape_ids': ['A', 'B', 'C', 'D'],
                'effects': [{'required_count': 2}, {'required_count': 4}]}
        suits, cores, shapes = {'set': suit}, {'core': 'set'}, frozenset('ABCDE')
        def count(geometries):
            items = ({'kind': 'core', 'item_id': 'core'},) + tuple(
                {'kind': 'module', 'geometry': shape} for shape in geometries)
            return active_set_count(items, suits, cores, shapes)
        self.assertEqual(count('ABCD'), 4)
        self.assertEqual(count('ABC'), 2)
        self.assertEqual(count('AAAAEEEE'), 0)
        self.assertEqual(count('AABBEEEE'), 2)
        self.assertEqual(count(''), 0)
        self.assertEqual(active_set_count((), suits, cores, shapes), 0)
        self.assertIsNone(active_set_count(None, suits, cores, shapes))
        self.assertIsNone(count(['']))
        self.assertIsNone(count(['unrecognized']))

    def test_partial_game_loadout_does_not_claim_complete_score(self):
        from src.services.practice_share_service import _loadout_score
        self.assertIsNone(_loadout_score({'_game_importable': False, 'total_score': 301.3}))

    def test_complete_game_score_keeps_existing_total_grade_and_zero(self):
        from src.optimizer.contracts import EQUIP_SCORE, EQUIP_UID, ROLE_EQUIPPED_DRIVES, ROLE_TOTAL_SCORE
        from src.services.practice_share_service import _loadout_score
        state = {'_game_mode': True, '_game_importable': True, ROLE_TOTAL_SCORE: 0,
                 ROLE_EQUIPPED_DRIVES: [{EQUIP_UID: 'fixture-item', EQUIP_SCORE: 0}]}
        self.assertEqual(_loadout_score(state), 0)
        state[ROLE_TOTAL_SCORE] = 301.3
        state[ROLE_EQUIPPED_DRIVES][0][EQUIP_SCORE] = 301.3
        self.assertEqual(_loadout_score(state), 301.3)
        state[ROLE_TOTAL_SCORE] = 301.4
        self.assertIsNone(_loadout_score(state))

    def test_game_role_share_uses_game_items_and_current_equipment_label(self):
        from types import SimpleNamespace
        from PySide6.QtWidgets import QApplication, QWidget
        from src.features.inventory.loadout_share_dialog import loadout_share_button
        app = QApplication.instance() or QApplication([])
        owner = QWidget()
        owner.roles_db = {'示例': {'weights': {'暴击率%': 1}}}
        owner.app_context = SimpleNamespace(generation=1,
            account=SimpleNamespace(user_database_path=Path('unused')),
            paths=SimpleNamespace(static_database_path=Path('unused'), game_ui_asset_root=Path('unused'),
                                  bundled_config_dir=Path('unused')))
        raw = {'item_id': 'fixture-game-item'}
        state = {'_game_mode': True, '_game_projection': SimpleNamespace(items=(raw,))}
        with patch('src.features.inventory.loadout_share_dialog.LoadoutShareDialog') as dialog:
            button = loadout_share_button(owner, role_name='示例', state=state, score=301.3, size=34)
            button.click()
            request = dialog.call_args.args[0]
            self.assertEqual(request.state['_official_items'], [raw])
            self.assertIsNot(request.state['_official_items'][0], raw)
            self.assertEqual(request.slot_name, '当前装备')
            self.assertEqual(request.score, 301.3)
            self.assertNotIn('_official_items', state)
        owner.close()
        app.processEvents()

    def test_account_read_returns_only_current_account_and_does_not_write(self):
        with TemporaryDirectory() as directory:
            database = Path(directory)/'account.sqlite3'
            with UserDataDao(database, account_id='practice-fixture') as dao:
                before = dao._db().total_changes
                data = dao.read_practice_share_context(())
                self.assertEqual(dao._db().total_changes, before)
                self.assertEqual(data, {'profiles': [], 'observations': []})

    def test_statistics_context_reads_only_requested_equipped_roles(self):
        with TemporaryDirectory() as directory:
            with UserDataDao(Path(directory)/'account.sqlite3', account_id='practice-fixture') as dao:
                for identity in (1, 2):
                    dao.save_character_profile(character_id=identity, character_level=60, breakthrough_stage=4,
                                               awakening_level=0, fork_id=None, fork_level=None,
                                               fork_breakthrough_stage=None, fork_refinement_level=None)
                data = dao.read_practice_share_context((1,))
                self.assertEqual([row['character_id'] for row in data['profiles']], [1])
                self.assertEqual(data['observations'], [])

    def test_saved_share_buttons_bind_each_slot_not_the_current_selection(self):
        from types import SimpleNamespace
        from PySide6.QtWidgets import QApplication, QWidget
        from src.features.inventory.loadout_share_dialog import loadout_share_button
        app = QApplication.instance() or QApplication([])
        owner = QWidget()
        owner.roles_db = {'示例': {'weights': {'暴击率%': 1}}}
        owner.app_context = SimpleNamespace(generation=1,
            account=SimpleNamespace(user_database_path=Path('unused')),
            paths=SimpleNamespace(static_database_path=Path('unused'), game_ui_asset_root=Path('unused'),
                                  bundled_config_dir=Path('unused')))
        first = {'_character_id': 1, '_loadout_slot_id': 11, '_loadout_slot_name': '主力',
                 'equipped_drives': [{'uid': 'first'}], '_official_items': [{'item_id': 'first'}]}
        second = {'_character_id': 1, '_loadout_slot_id': 12, '_loadout_slot_name': '备用',
                  'equipped_drives': [{'uid': 'second'}], '_official_items': [{'item_id': 'second'}]}
        buttons = [loadout_share_button(owner, role_name='示例', state=state, score=score, size=34)
                   for state, score in ((first, 100), (second, 200))]
        with patch('src.features.inventory.loadout_share_dialog.LoadoutShareDialog') as dialog:
            buttons[1].click()
            second_request = dialog.call_args.args[0]
            buttons[0].click()
            first_request = dialog.call_args.args[0]
            self.assertEqual((first_request.slot_name, first_request.score, first_request.state['_loadout_slot_id']),
                             ('主力', 100, 11))
            self.assertEqual((second_request.slot_name, second_request.score, second_request.state['_loadout_slot_id']),
                             ('备用', 200, 12))
            self.assertEqual(first_request.state['_official_items'][0]['item_id'], 'first')
            self.assertEqual(second_request.state['_official_items'][0]['item_id'], 'second')
            self.assertIsNot(first_request.state['_official_items'], first['_official_items'])
        owner.close()
        app.processEvents()

    def test_cancelled_generation_does_not_render_or_read_account(self):
        request = PracticeShareRequest(Path('unused'), Path('unused'), Path('unused'), ())
        with patch('src.services.practice_share_service.UserDataDao') as dao:
            with self.assertRaises(CancelledError):
                build_practice_share_panel(request, cancelled=lambda: True)
            dao.assert_not_called()
        with self.assertRaises(CancelledError):
            render_practice_share_png(PracticeSharePanel((PracticeEntry(1, '示例'),)), cancelled=lambda: True)

    def test_config_is_disclosed_in_image_notes_not_as_measured_cultivation(self):
        panel = PracticeSharePanel((PracticeEntry(1, '示例', level=PracticeValue('80', True)),))
        self.assertIn('配置', panel.notes[0])
        self.assertEqual(replace(panel.entries[0], score=0).score, 0)


if __name__ == '__main__':
    unittest.main()
