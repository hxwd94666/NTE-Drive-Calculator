# 验证未完整角色只写参考表，保留未知和占位说明。
import json
import sqlite3
from types import SimpleNamespace

from tools.game_data.static_database_reference_characters import import_reference_character


def test_reference_character_preserves_known_text_without_calculation_row():
    connection = sqlite3.connect(':memory:')
    connection.execute('CREATE TABLE character(character_id INTEGER)')
    connection.execute('CREATE TABLE reference_character(character_id, name_zh, reason_zh, sections_json, source_row_id)')
    builder = SimpleNamespace(connection=connection, source_row_id=lambda *_: 17, awaken_rows={}, rows={
        'character_abilities': {'1074': {'CharacterAbilityList': [{'Key': 'GA_Exe_Skill'}]}},
        'gameplay_ability_tips': {'GA_Exe_Skill': {'Desc': {'TableId': '/Game/Text/Exe', 'Key': 'skill',
                                                       'LocalizedString': '倍率 {0}，其余未提供'}}},
    })
    import_reference_character(builder, '1074', {'ItemName': {'SourceString': 'Exe'}}, '好感度数据未完整')
    row = connection.execute('SELECT * FROM reference_character').fetchone()
    assert row[:3] == (1074, 'Exe', '好感度数据未完整')
    sections = json.loads(row[3])
    assert ['属性', '未提供'] in sections[0]['fields']
    assert ['skill', '倍率 {0}，其余未提供'] in sections[1]['fields']
    assert connection.execute('SELECT COUNT(*) FROM character').fetchone()[0] == 0
    connection.close()
