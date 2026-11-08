# 用冻结的已保存配装偏好建立额外见证，避免把配置存在误判为无法弃置。
from concurrent.futures import CancelledError
from pathlib import Path
from dataclasses import replace

from src.services.allocation.context_builder import build_allocation_context, read_allocation_scoring_rules
from src.services.allocation.legacy_adapter import run_legacy_allocation
from src.storage.sqlite.user_data_dao import UserDataDao
from src.storage.sqlite.static_game_data_dao import StaticGameDataDao


def _summary(run):
    result = {}
    for character_id, key in run.role_keys:
        plan = run.plans.get(key) or {}
        items = [*plan.get('assigned_set_drives', ()), *plan.get('assigned_extra_drives', ())]
        if plan.get('assigned_tape') is not None:
            items.append(plan['assigned_tape'])
        uids = []
        for item in items:
            candidate = run.candidates_by_legacy_uid[item.uid]
            prefix = 'module' if candidate.kind == 'module' else 'core'
            uids.append(f'nte-{prefix}-{candidate.uid_slot}-{candidate.uid_serial}')
        result[str(character_id)] = {'valid': bool(plan.get('valid', False)),
                                    'score': float(plan.get('score') or 0), 'uids': uids,
                                    'has_core':plan.get('assigned_tape') is not None}
    return result


def _run(context, cancelled, excluded=(), executor=None):
    # 旧档案的通用属性约束尚非 allocation_v2 能力；不丢弃约束来强行走快路径。
    supported = (executor is not None and context.allocation_strategy == 'role_priority'
                 and all(not role.property_limits and role.crit_threshold is None for role in context.roles))
    if not supported:
        return run_legacy_allocation(context, excluded_uids=excluded, cancel_check=cancelled)
    def execute(request, scorer):
        frozen = replace(request, roles_db={r: {**data, 'crit_source_known': False}
                                          for r, data in request.roles_db.items()})
        return executor(frozen, scorer)
    return run_legacy_allocation(context, excluded_uids=excluded, cancel_check=cancelled,
                                 kernel_executor=execute)


def freeze_profile_witnesses(account, static, config_dir, snapshot_id, checkpoint, cancelled, *, executor=None, excluded_uids=frozenset()):
    """只取启用档案的最新版本；历史版本不变成当前约束，失败则禁止本次弃置。"""
    witnesses, issues = [], []
    scoring = read_allocation_scoring_rules(Path(config_dir) / 'stats.json')
    with UserDataDao(account) as user, StaticGameDataDao(static) as data:
        profiles = user.list_optimization_profiles()
        for profile in profiles:
            checkpoint()
            version = profile.get('version') or {}
            if not version.get('characters'):
                continue
            try:
                context = build_allocation_context(user, data, scoring_rules=scoring, snapshot_id=snapshot_id,
                    profile_id=profile['profile_id'], profile_version=version['version_number'])
                if excluded_uids:
                    context = replace(context, candidates=tuple(c for c in context.candidates if
                        f"nte-{'module' if c.kind == 'module' else 'core'}-{c.uid_slot}-{c.uid_serial}" not in excluded_uids))
                before = _summary(_run(context, cancelled, executor=executor))
                if any(not row['valid'] for row in before.values()):
                    issues.append('已保存配装偏好的部分角色无法形成有效方案，本次不产生弃置。')
                witnesses.append((context, before))
            except (CancelledError, TimeoutError):
                raise
            except Exception:
                issues.append('已保存配装偏好的冻结或复算失败，本次不产生弃置。')
    return witnesses, list(dict.fromkeys(issues))


def witness_uids(witnesses):
    return frozenset(uid for _context, before in witnesses for row in before.values()
                     if row['valid'] for uid in row['uids'])


def verify_profile_removal(witnesses, removed, checkpoint, cancelled):
    if not removed:
        return []
    regressions = []
    for context, before in witnesses:
        checkpoint()
        # 上游规范化身份包含 kind；到正式 Context 的排除接口只投影当前已核对候选的 UID。
        after = run_profile_removal(context,removed,cancelled)
        for character_id, prior in before.items():
            current = after.get(character_id, {})
            if prior['valid'] and (not current.get('valid') or current.get('score', 0) + 1e-7 < prior['score']
                                  or prior.get('has_core') and not current.get('has_core')):
                regressions.append(f'profile:{context.profile_id}:{character_id}')
    return regressions


def run_profile_removal(context, removed, cancelled, *, executor=None):
    excluded = {c.uid for c in context.candidates
                if f"nte-{'module' if c.kind == 'module' else 'core'}-{c.uid_slot}-{c.uid_serial}" in removed}
    return _summary(_run(context, cancelled, excluded, executor))
