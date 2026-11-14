# 从正式静态目录直接生成全角色规则草稿，不读取账号库存或执行配装计算。
from src.domain.equipment.warehouse.intelligent_rules import generated_rules, RULE_SCHEME_VERSION, policy_from_rules
from src.services.allocation.inputs.inventory import legacy_shape_id
from src.storage.sqlite.static_game_data_dao import StaticGameDataDao


def generate_warehouse_rules(static_database, rules, *, grade_choice=None):
    policy_from_rules(rules)  # 拒绝同种动态规则多条启用，不能猜测顶部参数归属。
    def current(kind):
        candidates = [r for r in rules if r.get('smart_kind') == kind]
        enabled = [r for r in candidates if r.get('enabled', True)]
        return enabled[0] if enabled else candidates[0] if len(candidates) == 1 else None
    future, grade = current('future'), current('grade')
    options = {'target_inventory_size': future.get('target_inventory_size', 1000) if future else 1000,
               'grade': grade.get('grade', 'SSS') if grade and grade.get('enabled', True)
                        else 'none' if grade else 'SSS'}
    if grade_choice is not None:
        options['grade'] = grade_choice
    with StaticGameDataDao(static_database) as static:
        suits = {str(row['suit_id']): row['name_zh'] for row in static.list_suits()}
        shapes = [legacy_shape_id(row['shape_id']) for row in static.list_shapes()]
    return {'options': {'rule_scheme_version': RULE_SCHEME_VERSION, 'last_drive_grade':options['grade']},
            'rules': generated_rules(options, strong_set_name=suits.get('Suit6'),
                                     support_set_name=suits.get('Suit11'),
                                     all_sets=list(suits.values()), all_shapes=shapes)}
