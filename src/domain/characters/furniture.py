# 按正式家具身份投影已确认的永久属性；缺失、重复和非法观测均保留旧值。
from collections.abc import Mapping, Sequence


# DT_SpecialFurniture 与 DT_SpecialFurnitureModifyData 的无条件加法属性。
# 固定逐级表是静态字段映射，不解析描述，也不包含载具、收益或体力消耗家具。
FURNITURE_FIELDS = {
    'SF_0011': ('yaodao_attack_add', 'AtkAdd', (0., 2., 4., 6., 8., 10., 12., 14., 16., 18., 20.)),
    'SF_0012': ('quantao_crit_damage', 'CritDamageBase',
                (0., .004, .008, .012, .016, .020, .024, .028, .032, .036, .040)),
    'SF_0013': ('kaijia_defense_add', 'DefAdd', (0., 3., 6., 9., 12., 15., 18., 21., 24., 27., 30.)),
}


def native_furniture_values(records: Sequence[Mapping]) -> dict[str, float]:
    values = {}
    for identity, (field, _property, levels) in FURNITURE_FIELDS.items():
        matches = [row for row in records if isinstance(row, Mapping)
                   and row.get('kind') == 'furniture_raw' and row.get('FurnitureID') == identity]
        if len(matches) != 1:
            continue
        row = matches[0]
        level, active = row.get('Level'), row.get('bIsActivated')
        if type(level) is not int or not 0 <= level < len(levels) or type(active) is not bool:
            continue
        values[field] = levels[level] if active else 0.0
    return values
