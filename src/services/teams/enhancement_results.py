# 将一次明确队伍增强的已验证预览封成轻量信封，保存只消费该信封，不从当前槽位拼历史。
from dataclasses import dataclass
import json
from uuid import uuid4
from src.integrations.static_catalog.static_catalog_release import StaticCatalogReleaseReader, static_catalog_read_unit

from src.domain.teams.enhancement import (
    PROVENANCE_KEYS, SEARCH_KEYS, TeamResultCommit, TeamResultCommitEntry, validate_team_result_payload,
)
from src.services.allocation.results.postprocess_facts import allocation_inputs_digest, allocation_save_projection, allocation_row_save_projection
from src.storage.sqlite.user_data_dao import UserDataDao


@dataclass(frozen=True)
class TeamEnhancementInput:
    binding: object
    source: object
    search_mode: str
    static_release: object


@dataclass(frozen=True)
class TeamEnhancementPreview:
    commit: TeamResultCommit
    row_digests: tuple[tuple[str, str], ...]
    generation: int


def freeze_team_result_inputs(selection, database_path):
    with UserDataDao(database_path) as dao, dao.read_consistent_state():
        current = dao.list_teams()
        for binding in selection.teams:
            if (binding.team not in current or dao.profile()["account_id"] != binding.axis.account_id):
                raise ValueError("队伍增强输入已失效，请重新配置")
        releases = {}
        result = []
        for binding in selection.teams:
            path = binding.axis.static_database_path
            if path not in releases:
                with static_catalog_read_unit(path) as unit:
                    releases[path] = unit.release
            result.append(TeamEnhancementInput(binding, dao.get_team_source(binding.team.team_id), selection.search_mode, releases[path]))
        return tuple(result)


def _payload(info, metadata, *, team_index, joint):
    binding, source = info.binding, info.source
    battle = binding.axis.battle
    kind = "optimized" if metadata.get("status") == "completed" else "baseline"
    own = None
    if joint and kind == "optimized":
        reports = metadata.get("team_reports")
        if not isinstance(reports, list) or len(reports) != 2:
            raise ValueError("联合增强缺少每队独立结果，不能发布单队简报")
        own = reports[team_index]
        if own.get("battle_record_id") != battle.battle_record_id:
            raise ValueError("联合增强队伍与战报范围身份不一致")
        before, after = own["baseline"]["candidate_value"], own["selected_result"]["candidate_value"]
    else:
        before, after = metadata.get("baseline_value"), metadata.get("candidate_value")
    if kind == "baseline":
        # 基础结果缺少已证实的目标值时保留未知，不补零，也不搬用另一组收益。
        before = after = None
    coverage = (own["selected_result"].get("coverage") if own else metadata.get("coverage"))
    if coverage is not None:
        coverage = {key: coverage[key] for key in ("policy", "basis_damage", "quantified_damage", "estimated_damage", "excluded_damage")}
    search = {key: metadata[key] for key in SEARCH_KEYS if key in metadata}
    search.update(search_mode=info.search_mode,
                  blueprint_enumeration_complete=(metadata.get("blueprint_usage") or {}).get("complete"))
    payload = dict(version=1, team_revision=binding.team.revision, character_ids=sorted(binding.team.character_ids),
        source_mode=source.mode, source_revision=source.revision, source_bindings=[list(pair) for pair in source.bindings],
        scope=dict(record_id=battle.battle_record_id, scope="range" if battle.start_us is not None else battle.detail_scope or "all",
                   start_us=battle.start_us, end_us=battle.end_us), anchor_mode=binding.axis.anchor_mode, result_kind=kind,
        baseline_value=before, candidate_value=after, relative_gain=after/before-1 if kind == "optimized" else None,
        coverage=coverage, search=search,
        provenance={key: metadata[key] for key in PROVENANCE_KEYS if metadata.get(key) is not None},
        source_input_digest=(own.get("source_input_digest") if own else metadata.get("source_input_digest"))
            or binding.axis.expected_source_input_digest or binding.axis.team_source_input_digest,
        baseline_build_sha256=metadata.get("baseline_build_sha256"))
    if metadata.get("dataset_id") != info.static_release.dataset_id:
        raise ValueError("队伍结果与开始增强时冻结的静态资料不一致")
    payload["provenance"]["static_sha256"] = info.static_release.sha256
    return validate_team_result_payload(payload)


def attach_team_result_preview(plans, inputs, database_path):
    """仅完整合法且有本次伤害/B0 凭证的目标队伍生成持久结果信封。"""
    required = [(role, key) for info in inputs for role, key in zip(info.binding.roles, info.binding.team.character_ids)]
    if any(not (plans.get(role) or {}).get("valid") or not plans[role].get("damage_optimization") for role, _ in required):
        return plans
    entries, digests = [], []
    for index, info in enumerate(inputs):
        StaticCatalogReleaseReader().ensure_unchanged(info.static_release)
        binding = info.binding
        local = [plans[role]["damage_optimization"] for role in binding.roles]
        metadata = next((value for value in local if value.get("status") == "completed"), local[0])
        roles = tuple(zip(binding.roles, binding.team.character_ids))
        entries.append(TeamResultCommitEntry(binding.team, info.source, roles,
            _payload(info, metadata, team_index=index, joint=len(inputs) == 2)))
        for role, key in roles:
            plan = plans[role]
            digests.append((role, allocation_inputs_digest(allocation_save_projection(
                role, plan, plan["damage_optimization"]["source_snapshot_id"], key))))
    account_id, generation = inputs[0].binding.axis.account_id, inputs[0].binding.axis.generation
    run_id = uuid4().hex
    joint_json = None
    if len(entries) == 2:
        completed = next((plans[role]["damage_optimization"] for role, _ in required
                          if plans[role]["damage_optimization"].get("status") == "completed"), None)
        joint_json = json.dumps(dict(version=1, team_ids=[entry.team.team_id for entry in entries],
            normalized_baseline=completed["baseline_value"] if completed else None,
            normalized_candidate=completed["candidate_value"] if completed else None,
            relative_gain=completed["candidate_value"]-1 if completed else None), sort_keys=True)
    digest = allocation_inputs_digest(dict(run_id=run_id, rows=digests, results=[entry.payload_json for entry in entries], joint=joint_json))
    commit = TeamResultCommit(run_id, account_id, digest, tuple(entries), joint_json)
    with UserDataDao(database_path) as dao:
        dao.assert_team_result_context(commit)
    preview = TeamEnhancementPreview(commit, tuple(digests), generation)
    for role, _ in required:
        plans[role]["team_enhancement_preview"] = preview
    return plans


def team_result_commit_for_rows(dao, rows, *, check_current=True):
    previews = [row.get("team_enhancement_preview") for row in rows if row.get("team_enhancement_preview") is not None]
    if not previews:
        return None
    preview = previews[0]
    if not isinstance(preview, TeamEnhancementPreview) or any(other != preview for other in previews):
        raise ValueError("队伍结果来自不同预览作业")
    expected_roles = [role for entry in preview.commit.entries for role, _ in entry.roles]
    if (len(preview.row_digests) != len(expected_roles) or {role for role, _ in preview.row_digests} != set(expected_roles)
            or preview.commit.input_digest != allocation_inputs_digest(dict(run_id=preview.commit.run_id,
                rows=list(preview.row_digests), results=[entry.payload_json for entry in preview.commit.entries], joint=preview.commit.joint_summary_json))):
        raise ValueError("队伍结果信封与本次冻结构筑摘要不一致")
    by_role = {row["role_name"]: row for row in rows}
    if len(by_role) != len(rows):
        raise ValueError("队伍保存集合包含重复角色")
    for role, expected in preview.row_digests:
        row = by_role.get(role)
        if (row is None or row.get("team_enhancement_preview") != preview
                or allocation_inputs_digest(allocation_row_save_projection(row)) != expected):
            raise ValueError("队伍增强须保存本次完整构筑，成员或冻结评分已改变")
    if dao.profile()["account_id"] != preview.commit.account_id:
        raise ValueError("队伍结果属于其他账号")
    if check_current:
        dao.assert_team_result_context(preview.commit)
    return preview.commit


def check_completed_team_save(dao, rows):
    commit = team_result_commit_for_rows(dao, rows, check_current=False)
    if commit is None:
        return None
    ids = dao.completed_team_run(commit.run_id, commit.input_digest)
    if ids is None:
        return None
    plans = {plan["plan_id"]: plan for plan in dao.get_loadout_plans(ids)}
    if len(rows) != len(ids):
        raise ValueError("该作业已保存，当前保存集合不同，请核对增强历史")
    for row, plan_id in zip(rows, ids):
        plan = plans.get(plan_id)
        if (plan is None or plan["character_id"] != row["character_id"]
                or row.get("slot_id") is not None and row["slot_id"] != plan["slot_id"]
                or row.get("snapshot_id") != plan["source_snapshot_id"] or row.get("score") != plan["score"]
                or (row.get("payload") or {}).get("assignment_scores") != (plan.get("payload") or {}).get("assignment_scores")):
            raise ValueError("该作业已有保存记录，目标或方案已变化，请核对增强历史，不重复写入")
    return len(ids)
