# 验证原生编辑事实直接读最小账号表，并保持 Python 证据判据与编辑输入边界。
from concurrent.futures import CancelledError
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from src.integrations.battle_analysis.editor_facts import load_editor_facts
from src.integrations.nte_analysis_core import NativeAnalysisError, NteAnalysisCoreClient
from src.services.battle_report.characters.inferred_facts import BattleInferredCharacterFactService
from src.services.battle_report.history.projection import character_analysis_scopes
from tests.battle_report.build.test_battle_build_editor_seed import BattleBuildEditorSeedTests


class NativeEditorFactsAdapterTests(unittest.TestCase):
    def test_history_native_branch_never_loads_or_analyzes_axis_in_python(self):
        fixture = BattleBuildEditorSeedTests()
        fixture.setUp()
        fixture.service.native_page_loader = Mock()
        fixture.service.native_page_loader.load_editor_facts.return_value = ((), {1036: 'second'})
        with (
            patch.object(fixture.dao, 'load_battle_axis_evidence', side_effect=AssertionError('Python axis read')),
            patch.object(BattleInferredCharacterFactService, 'infer', side_effect=AssertionError('Python inference')),
        ):
            detail = fixture._load(seed_from_role_page=False)
        self.assertEqual(detail['analysis_detail_scope'], 'second')
        fixture.service.native_page_loader.load_editor_facts.assert_called_once_with(41)

    def test_adapter_freezes_selectors_and_cancels_stale_results(self):
        dependencies = SimpleNamespace(account_id='fixture', generation=4, user_database_path=Path('unused.sqlite3'))
        client = Mock(supports_battle_page=True)
        client.load_battle_page.return_value = {'editor_facts': {'inferred_character_facts': [], 'character_analysis_scopes': []}}
        self.assertEqual(load_editor_facts(client, dependencies, lambda _: True, 7), ((), {}))
        self.assertEqual(set(client.load_battle_page.call_args.args[0]), {
            'account_id', 'generation', 'user_database_path', 'battle_record_id', 'detail_level',
        })
        with self.assertRaises(CancelledError):
            load_editor_facts(client, dependencies, lambda _: False, 7)
        guard = Mock(side_effect=(True, False))
        with self.assertRaises(CancelledError):
            load_editor_facts(client, dependencies, guard, 7)

    def test_adapter_rejects_malformed_or_duplicate_half_mapping(self):
        dependencies = SimpleNamespace(account_id='fixture', generation=4, user_database_path=Path('unused.sqlite3'))
        client = Mock(supports_battle_page=True)
        for scopes in ([{'character_id':True,'scope':'first'}],
                       [{'character_id':1004,'scope':'upper'}],
                       [{'character_id':1004,'scope':None}] * 2):
            client.load_battle_page.return_value = {'editor_facts': {'inferred_character_facts': [], 'character_analysis_scopes': scopes}}
            with self.assertRaises(NativeAnalysisError):
                load_editor_facts(client, dependencies, lambda _: True, 7)


@unittest.skipUnless(os.environ.get('NTE_ANALYSIS_CORE_TEST_EXE'), 'explicit isolated native executable required')
class NativeEditorFactsDatabaseTests(unittest.TestCase):
    def test_minimal_database_direct_read_matches_oracle_and_never_writes(self):
        hits = [
            dict(sequence_text='12', sequence_order=12, relative_time_us=1, abyss_half=' UPPER ', character_id=1004,
                 direction='outgoing', gameplay_effect_name='GE_Player_Lacrimosa_Blood_Damage_LV6'),
            dict(sequence_text='12', sequence_order=13, relative_time_us=2, abyss_half='lower', character_id=1004,
                 direction='outgoing', gameplay_effect_name='GE_Player_Lacrimosa_Blood_Damage_LV6'),
            dict(sequence_text='', sequence_order=14, relative_time_us=3, abyss_half='lower', character_id=1036,
                 direction='incoming', gameplay_effect_name='GE_Player_Lacrimosa_Blood_Damage_LV6'),
            dict(sequence_text='', sequence_order=15, relative_time_us=4, abyss_half='unknown', character_id=1072,
                 direction='outgoing', gameplay_effect_name='GE_Player_Lacrimosa_Blood_Damage_LV6_other'),
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'account.sqlite3'
            with sqlite3.connect(path) as conn:
                conn.executescript('''CREATE TABLE database_profile(singleton_id INTEGER,account_id TEXT);
                    INSERT INTO database_profile VALUES(1,'fixture');
                    CREATE TABLE battle_axis_capture(capture_id INTEGER,battle_record_id INTEGER,capture_state TEXT);
                    INSERT INTO battle_axis_capture VALUES(1,7,'finalized');
                    CREATE TABLE battle_hit_evidence(capture_id INTEGER,sequence_text TEXT,sequence_order INTEGER,
                    relative_time_us INTEGER,abyss_half TEXT,character_id INTEGER,direction TEXT,gameplay_effect_name TEXT);''')
                conn.executemany('INSERT INTO battle_hit_evidence VALUES(1,:sequence_text,:sequence_order,:relative_time_us,:abyss_half,:character_id,:direction,:gameplay_effect_name)', hits)
            conn.close()
            before = hashlib.sha256(path.read_bytes()).digest()
            dataset = json.loads(Path('data/manifest.json').read_text(encoding='utf-8'))['database']['dataset_id']
            client = NteAnalysisCoreClient.from_executable(Path(os.environ['NTE_ANALYSIS_CORE_TEST_EXE']), dataset)
            dependencies = SimpleNamespace(account_id='fixture', generation=4, user_database_path=path)
            facts, scopes = load_editor_facts(client, dependencies, lambda _: True, 7)
            self.assertEqual([asdict(f) for f in facts], [asdict(f) for f in BattleInferredCharacterFactService.infer({'hits': hits})])
            self.assertEqual(scopes, character_analysis_scopes({'hits': hits}))
            self.assertEqual(load_editor_facts(client, dependencies, lambda _: True, 8), ((), {}))
            self.assertEqual(before, hashlib.sha256(path.read_bytes()).digest())
            dependencies.account_id = 'other'
            with self.assertRaises(NativeAnalysisError):
                load_editor_facts(client, dependencies, lambda _: True, 7)


if __name__ == '__main__':
    unittest.main()
