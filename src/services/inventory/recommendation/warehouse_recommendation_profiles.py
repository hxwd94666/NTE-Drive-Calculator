# 用冻结的已保存配装偏好建立额外见证，避免把配置存在误判为无法弃置。
from src.observability.stage_timings import timed, timed_call
from concurrent.futures import CancelledError
from dataclasses import replace

from src.domain.localization.catalog import localized_text
from src.services.allocation.inputs.context_builder import build_allocation_context, read_allocation_scoring_rules
from src.services.allocation.execution.legacy_adapter import run_legacy_allocation

_LOCALE_PREFIX = "inventory.recommendation_profiles."


def _text(text_catalog, key: str, fallback: str, **values: object) -> str:
    """本文件用户可见提示统一挂在本模块前缀下；目录缺该 key 时回退原中文基线。"""

    return localized_text(text_catalog, f"{_LOCALE_PREFIX}{key}", fallback, **values)


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


class ProfileConstraintsUnavailable(RuntimeError):
    """当前组件没有该档案所需的正式约束协议。"""


def run_profile_allocation(context, cancelled, excluded=(), *, executor, blueprint_executor, timings=None):
    # 旧档案约束不能省略，否则移除前后得分并不证明原方案仍可达到。
    if (context.allocation_strategy != 'role_priority'
            or any(role.property_limits or role.crit_threshold is not None for role in context.roles)):
        raise ProfileConstraintsUnavailable()
    def execute(request, scorer):
        frozen = replace(request, roles_db={r: {**data, 'crit_source_known': False}
                                          for r, data in request.roles_db.items()})
        return executor(frozen, scorer)
    return timed_call(timings, "witness.profile_native", run_legacy_allocation, context,
            excluded_uids=excluded, cancel_check=cancelled, kernel_executor=execute,
            blueprint_executor=blueprint_executor)


def materialize_profile_contexts(user, data, scoring_path, snapshot_id, checkpoint, *, timings=None,
                                text_catalog=None):
    """仅在输入 owner 的同一读事务中解析启用档案最新版本，搜索留到读取结束后。"""
    contexts, issues = [], []
    scoring = read_allocation_scoring_rules(scoring_path)
    for profile in user.list_optimization_profiles():
        checkpoint()
        version = profile.get('version') or {}
        if not version.get('characters'):
            continue
        try:
            contexts.append(timed_call(timings, "witness.profile_prepare", build_allocation_context, user, data,
                scoring_rules=scoring, snapshot_id=snapshot_id, profile_id=profile['profile_id'],
                profile_version=version['version_number']))
        except (CancelledError, TimeoutError):
            raise
        except Exception:
            issues.append(_text(
                text_catalog, "issue.profile_failed",
                "已保存配装偏好的冻结或复算失败，本次不产生弃置。"))
    return tuple(contexts), tuple(dict.fromkeys(issues))


@timed("witness.profiles")
def freeze_profile_witnesses(inputs, checkpoint, cancelled, *, solve, excluded_uids=frozenset(),
                             text_catalog=None):
    """搜索只消费冻结 context；不再回读账号或把历史版本变成当前约束。"""
    witnesses, issues = [], list(inputs.profile_issues)
    for context in inputs.profile_contexts:
        checkpoint()
        try:
            if excluded_uids:
                context = replace(context, candidates=tuple(c for c in context.candidates if
                    f"nte-{'module' if c.kind == 'module' else 'core'}-{c.uid_slot}-{c.uid_serial}" not in excluded_uids))
            before = _summary(solve(context, cancelled))
            if any(not row['valid'] for row in before.values()):
                issues.append(_text(
                    text_catalog, "issue.character_invalid",
                    "已保存配装偏好的部分角色无法形成有效方案，本次不产生弃置。"))
            witnesses.append((context, before))
        except (CancelledError, TimeoutError):
            raise
        except ProfileConstraintsUnavailable:
            issues.append(_text(
                text_catalog, "issue.constraints_unavailable",
                "分析组件暂不支持部分已保存配装的约束，本次无法验证相关弃置建议。"))
        except Exception:
            issues.append(_text(
                text_catalog, "issue.profile_failed",
                "已保存配装偏好的冻结或复算失败，本次不产生弃置。"))
    return witnesses, list(dict.fromkeys(issues))


def witness_uids(witnesses):
    return frozenset(uid for _context, before in witnesses for row in before.values()
                     if row['valid'] for uid in row['uids'])


def verify_profile_removal(witnesses, removed, checkpoint, cancelled, *, solve):
    if not removed:
        return []
    regressions = []
    for context, before in witnesses:
        checkpoint()
        # 上游规范化身份包含 kind；到正式 Context 的排除接口只投影当前已核对候选的 UID。
        after = run_profile_removal(context, removed, cancelled, solve=solve)
        for character_id, prior in before.items():
            current = after.get(character_id, {})
            if prior['valid'] and (not current.get('valid') or current.get('score', 0) + 1e-7 < prior['score']
                                  or prior.get('has_core') and not current.get('has_core')):
                regressions.append(f'profile:{context.profile_id}:{character_id}')
    return regressions


def run_profile_removal(context, removed, cancelled, *, solve):
    excluded = {c.uid for c in context.candidates
                if f"nte-{'module' if c.kind == 'module' else 'core'}-{c.uid_slot}-{c.uid_serial}" in removed}
    return _summary(solve(context, cancelled, excluded))
