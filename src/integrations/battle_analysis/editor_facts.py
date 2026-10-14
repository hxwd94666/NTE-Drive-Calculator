# 调用原生窄编辑事实入口，只传冻结身份和库路径，不读取或分析逐击。
from concurrent.futures import CancelledError
from pathlib import Path

from src.domain.battle_report.build.inputs import BattleInferredCharacterFact
from src.integrations.battle_analysis.page_wire import decode
from src.integrations.nte_analysis_core import NativeAnalysisError


def load_editor_facts(client, dependencies, context_is_current, battle_record_id):
    if client is None or not client.supports_battle_page:
        raise NativeAnalysisError('战报编辑事实需要配套数据库直读分析组件')

    def checkpoint():
        if not context_is_current(dependencies):
            raise CancelledError

    # 派发前和返回后均复核同一冻结依赖；过期回包上抛取消，不交给编辑副本。
    checkpoint()
    raw = client.load_battle_page({
        'account_id': dependencies.account_id, 'generation': dependencies.generation,
        'user_database_path': str(Path(dependencies.user_database_path).resolve()),
        'battle_record_id': battle_record_id, 'detail_level': 'editor',
    }, checkpoint=checkpoint)
    checkpoint()
    if not isinstance(raw, dict) or set(raw) != {'editor_facts'}:
        raise NativeAnalysisError('分析核心编辑事实响应字段不匹配')
    value = raw['editor_facts']
    if not isinstance(value, dict) or set(value) != {'inferred_character_facts', 'character_analysis_scopes'}:
        raise NativeAnalysisError('分析核心编辑事实格式无效')
    facts = decode(value['inferred_character_facts'], tuple[BattleInferredCharacterFact, ...])
    scopes = {}
    if not isinstance(value['character_analysis_scopes'], list):
        raise NativeAnalysisError('分析核心角色半场归属格式无效')
    for row in value['character_analysis_scopes']:
        if (not isinstance(row, dict) or set(row) != {'character_id', 'scope'}
                or type(row['character_id']) is not int or row['character_id'] in scopes
                or row['scope'] not in (None, 'first', 'second')):
            raise NativeAnalysisError('分析核心角色半场归属无效')
        scopes[row['character_id']] = row['scope']
    return facts, scopes
