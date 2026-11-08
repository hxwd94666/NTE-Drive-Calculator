# 将冻结建议投影为本次状态目标，供预览、移除复算与采用校验共用。
def recommendation_targets(rows):
    targets={}
    for row in rows:
        if row.get('predecessor_fixed'):
            target = row.get('fixed_state_target')
            before = 'locked' if row.get('original_locked') else 'discarded' if row.get('original_discarded') else 'normal'
            if row.get('state_known', True) and target in {'normal', 'locked', 'discarded'} and target != before:
                targets[row['uid']] = target
            continue
        if row.get('original_locked') or not row.get('state_known',True):
            continue
        if row['decision']=='lock':
            targets[row['uid']]='locked'
        elif row['decision']=='discard' and not row.get('original_discarded') and not row.get('original_equipped'):
            targets[row['uid']]='discarded'
        elif row['decision']=='keep' and row.get('original_discarded'):
            targets[row['uid']]='normal'
    return targets


def discarded_after_targets(rows, targets):
    return {row['uid'] for row in rows
            if targets.get(row['uid'],'discarded' if row.get('original_discarded') else 'normal')=='discarded'}
