# 验证机制节点关联、来源保留和独立选择，禁止将描述数值变成常驻属性。
import json
import sqlite3
import unittest
from pathlib import Path
from types import SimpleNamespace

from tools.game_data.static_database_feast_mechanisms import import_mechanisms, option_score
from tools.game_data.static_database_build_support import StaticDatabaseError


class FeastMechanismDataTests(unittest.TestCase):
    def test_current_score_spelling_preserves_zero_and_rejects_conflicts(self):
        self.assertEqual(option_score({'score': 25}), 25)
        self.assertEqual(option_score({'Score': 0, 'score': 0}), 0)
        self.assertIsNone(option_score({}))
        for node in ({'Score': 50, 'score': 25}, {'score': True}, {'score': -1}):
            with self.subTest(node=node), self.assertRaises(StaticDatabaseError):
                option_score(node)

    def fixture(self):
        option = 'DiyBoss_Test_01'
        node = {'OptionDesc': {'TableId': '/Game/Text/ST_UI_hpy.ST_UI_hpy', 'Key': option},
                'Score': 50, 'PlayerBuffs': [{'AssetPathName': '/Game/Test.Player_C'}], 'BossBuffs': []}
        return option, node, {option: {'OptionType': 'EDiyBossOptionType::MechanismBuff', 'OptionNode': node}}

    def test_missing_export_node_is_not_silently_promoted(self):
        option, _, options = self.fixture()
        options[option]['OptionNode'] = None
        builder = SimpleNamespace(rows={'feast_options': options, 'feast_mechanism_text': {}})
        with self.assertRaisesRegex(StaticDatabaseError, '补丁全局索引'):
            import_mechanisms(builder)

    def test_static_import_keeps_conditional_effects_and_independent_switches(self):
        option, node, options = self.fixture()
        with sqlite3.connect(':memory:') as c:
            c.executescript('''
                CREATE TABLE source_row(source_row_id INTEGER PRIMARY KEY);
                INSERT INTO source_row VALUES(1),(2);
                CREATE TABLE feast_option(option_id TEXT PRIMARY KEY,score INTEGER,effect_kind TEXT,add_value REAL);
                CREATE TABLE feast_stage_option(stage_id TEXT,category_ordinal INTEGER,option_ordinal INTEGER,category_name_zh TEXT,option_id TEXT);
                CREATE TABLE feast_stage_mechanism_option(stage_id TEXT,option_ordinal INTEGER,option_id TEXT,category_name_zh TEXT);
                INSERT INTO feast_stage_option VALUES('stage',0,0,'生命','life');
                INSERT INTO feast_stage_mechanism_option VALUES('stage',0,'DiyBoss_Test_01','额外挑战'),('stage',1,'DiyBoss_Test_02','额外挑战');
                INSERT INTO feast_option VALUES('DiyBoss_Test_01',NULL,'unknown',NULL);
            ''')
            schema = Path(__file__).resolve().parents[2] / 'src/storage/sqlite/schema/041_game_static_feast_mechanisms.sql'
            c.executescript(schema.read_text(encoding='utf-8'))
            builder = SimpleNamespace(connection=c, rows={
                'feast_options': options, 'feast_mechanism_text': {option: '生命低于50%时防御提高50%'},
            }, source_row_id=lambda table, key: 1 if table == 'feast_options' else 2)
            import_mechanisms(builder)
            self.assertEqual(c.execute('SELECT score,effect_kind,add_value FROM feast_option').fetchone(), (50, 'mechanism', None))
            self.assertEqual(c.execute('SELECT description_zh,source_row_id,text_source_row_id FROM feast_mechanism_detail').fetchone(), ('生命低于50%时防御提高50%', 1, 2))
            player, boss, raw = c.execute('SELECT player_buffs_json,boss_buffs_json,option_node_text FROM feast_mechanism_detail').fetchone()
            self.assertEqual(json.loads(player), ['/Game/Test.Player_C'])
            self.assertEqual(json.loads(boss), [])
            self.assertEqual(json.loads(raw), node)
            self.assertEqual(c.execute("SELECT category_ordinal FROM feast_all_stage_option WHERE category_name_zh LIKE '额外挑战%' ORDER BY category_ordinal").fetchall(), [(1,), (2,)])
