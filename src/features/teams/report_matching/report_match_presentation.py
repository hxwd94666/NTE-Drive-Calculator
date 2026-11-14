# 战报选择的只读文案投影：范围与状态由正式值决定，译文不参与业务分支。
from datetime import datetime

from src.ui.common.text_catalog import resolve_text_catalog


def scope_label(scope, text):
    if scope.scope == 'range':
        return text('teams.report.scope.range', start=f'{scope.start_us / 1_000_000:g}',
                    end=f'{scope.end_us / 1_000_000:g}')
    return text('teams.report.scope.' + scope.scope)


def candidate_parts(candidate, text):
    entry = candidate.entry
    scene = entry.environment_name or (text('teams.report.scene.abyss', floor=entry.abyss_floor)
            if entry.abyss_floor else text('teams.report.scene.unknown'))
    try:
        saved = datetime.fromisoformat(entry.saved_at_utc.replace('Z', '+00:00')).astimezone().strftime('%m-%d %H:%M')
    except ValueError:
        saved = entry.saved_at_utc
    return f'#{entry.battle_record_id} · {scope_label(candidate.range, text)}', scene, saved


def candidate_label(candidate, *, catalog=None):
    text = resolve_text_catalog(catalog).render
    scope, scene, saved = candidate_parts(candidate, text)
    return text('teams.report.candidate', scope=scope, scene=scene, time=saved)


def status_label(status, text):
    return text('teams.report.status.' + status.value)


def next_step(status, text):
    return text('teams.report.next.' + status.value)
