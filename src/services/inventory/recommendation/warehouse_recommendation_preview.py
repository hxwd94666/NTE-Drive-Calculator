# 一致性冻结推荐输入，预览只产出待保存状态；真正写入仍经仓库后验确认流程。
from __future__ import annotations

from src.observability.stage_timings import timed, timed_call

from copy import deepcopy
from concurrent.futures import CancelledError
from dataclasses import dataclass, field
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory

from src.domain.equipment.eligibility.management_settings import MatchingScope, normalize_warehouse_config
from src.domain.equipment.warehouse.warehouse_decisions import DecisionStatus, decision_row
from src.domain.equipment.warehouse.intelligent_rules import RULE_SCHEME_VERSION
from src.domain.equipment.warehouse.warehouse_targets import recommendation_targets
from src.services.inventory.management.management_settings import ManagementSettingsService
from src.services.inventory.recommendation.warehouse_predecessors import evaluate_predecessors
from src.services.inventory.management.warehouse_conflicts import freeze_conflict_details
from src.services.identification.matching_settings import MatchingSettingsService
from src.services.inventory.recommendation.warehouse_recommendation import run_warehouse_trial
from src.services.inventory.recommendation.warehouse_calculation_inputs import freeze_warehouse_calculation_inputs
from src.services.characters.workshop_weight_template_service import configured_workshop_weight_template_file
from src.storage.sqlite.inventory.warehouse_recommendation_inputs import recommendation_account_identity, warehouse_current_snapshot_id
from src.storage.sqlite.user_data_dao import UserDataDao


def file_identity(paths):
    return tuple((str(p.resolve()), p.stat().st_size, p.stat().st_mtime_ns) for p in paths)


def post_config(path):
    return ManagementSettingsService(path, scope=MatchingScope.WAREHOUSE).load()[0]


def inventory_state_identity(path, snapshot_id):
    with UserDataDao.read_only(path) as dao:
        return tuple(sorted((row['kind'], row['uid_slot'], row['uid_serial'], row.get('locked'),
            row.get('discarded'), row.get('equipped')) for row in dao.list_inventory_items_with_runtime_state(snapshot_id)))


@dataclass
class RecommendationGuard:
    database: Path
    snapshot_id: int
    account_identity: str
    files: tuple
    file_version: tuple
    matching: dict
    config: dict
    check_context: object
    required_retained_uids: frozenset[str] = frozenset()
    initially_discarded_uids: frozenset[str] = frozenset()
    state_identity: tuple = ()
    timings: object = field(default=None, repr=False, compare=False)

    def require_targets(self, targets):
        if not any(state=='discarded' for state in targets.values()):
            return
        missing=[uid for uid in self.required_retained_uids
                 if targets.get(uid,'discarded' if uid in self.initially_discarded_uids else 'normal')=='discarded']
        if missing:
            raise ValueError(f'有 {len(missing)} 件已验证配装所需装备未保留。请同时采用所需恢复建议，或重新计算后再弃置。')  # ui-text: internal

    @timed("guard.total")
    def require_current(self, *, check_snapshot=True, timings=None):
        self.check_context()
        if (
            timed_call(timings, "guard.account", recommendation_account_identity, self.database) != self.account_identity
            or file_identity(self.files) != self.file_version
            or MatchingSettingsService(self.database, scope=MatchingScope.WAREHOUSE).load() != self.matching
            or post_config(self.database) != self.config
        ):
            raise ValueError("仓库建议的配装、权重、资料或规则已变化，请重新生成建议")
        if check_snapshot:
            with UserDataDao.read_only(self.database) as dao:
                if warehouse_current_snapshot_id(dao) != self.snapshot_id:
                    raise ValueError("背包快照已更新，请重新生成仓库建议")
            if self.state_identity and timed_call(timings, "guard.inventory_state", inventory_state_identity, self.database, self.snapshot_id) != self.state_identity:
                raise ValueError("装备状态已变化，请重新生成预览")

    def bind_saved_settings(self, config, matching):
        # 只承认这次编辑器提交的确切设置，不重读最新值冒充旧输入。
        self.config = normalize_warehouse_config(config)
        self.matching = deepcopy(matching)
        self.require_current()


def preview_targets(result):
    if result.get("violations"):
        if (result.get('loss_check') or {}).get('blocked_uids'):
            raise ValueError('全角色配装仍有已知损失，部分所需装备与明确弃置规则或状态保护冲突；请检查规则顺序后重算。')
        raise ValueError("整批校验未通过，不能采用建议")
    return recommendation_targets(result['rows'])


@timed("preview.total")
def build_preview(
    *, database, static_database, asset_root, config_dir, config, matching, options=None,
    check_context, cancel_check, user_targets=None, progress=lambda message: None, timings=None
):
    database, static_database, config_dir = map(Path, (database, static_database, config_dir))
    files = (static_database, static_database.with_name("manifest.json"), config_dir / "stats.json")
    # 评分库与公共图片可以分属不同目录；路径由组合根选定，不从数据库位置推算。
    asset_root = Path(asset_root)
    asset_manifest = asset_root / 'manifest.json'
    if asset_manifest.is_file():
        files += (asset_manifest,)
    template = configured_workshop_weight_template_file()
    if template is not None and template.is_file():
        files += (template,)
    version = file_identity(files)
    check_context()
    if cancel_check():
        raise CancelledError()
    identity = timed_call(timings, "input.account_identity", recommendation_account_identity, database)
    stored_matching = MatchingSettingsService(database, scope=MatchingScope.WAREHOUSE).load()
    stored_config = post_config(database)
    config = normalize_warehouse_config(config)
    intelligent = tuple(rule for rule in config['preserve_rules'] if rule.get('enabled') and
                        (rule.get('intelligent') or rule.get('smart_kind')))
    if intelligent and config.get('recommendation', {}).get('rule_scheme_version') != RULE_SCHEME_VERSION:
        raise ValueError('推荐已改为全角色参与，请重新生成智能规则并保存后再计算。人工规则会保留。')
    if config != stored_config or matching != stored_matching:
        raise ValueError('规则有未保存修改，请先保存后再计算建议。')
    with TemporaryDirectory(prefix="nte-warehouse-") as folder:
        root = Path(folder)
        (root / "data").mkdir()
        (root / "config").mkdir()
        for path in files[:2]:
            timed_call(timings, "input.static_copy", shutil.copy2, path, root / "data" / path.name)
            if timings is not None:
                try:
                    timings.note("input.static_copy", copied_bytes=(root / "data" / path.name).stat().st_size)
                except OSError:
                    pass  # 统计缺失不改变既有输入校验或取消策略。
        timed_call(timings, "input.config_copy", shutil.copy2, files[2], root / "config" / "stats.json")
        frozen = timed_call(timings, "input.freeze", freeze_warehouse_calculation_inputs,
            database, root / "data" / static_database.name, root / "config", include_profiles=bool(intelligent),
            cancel_check=cancel_check, timings=timings)
        if frozen.account_identity != identity:
            raise ValueError("冻结期间账号数据变化，请重新生成建议")
        data = frozen.values()
        if data['config'] != stored_config or data['matching'] != stored_matching:
            raise ValueError("冻结期间管理设置变化，请重新生成预览")
        batch = evaluate_predecessors(frozen, root / "data" / static_database.name, root / "config", config,
                                      user_targets=user_targets, cancel_check=cancel_check, timings=timings)
        remaining = any(value.status == DecisionStatus.UNDECIDED for value in batch.decisions.values())
        if intelligent and remaining:
            result = run_warehouse_trial(inputs=frozen, static_database=root / "data" / static_database.name,
                config_dir=root / "config", matching_settings=deepcopy(matching),
                preserve_rules=tuple(rule for rule in config['preserve_rules'] if rule.get('intelligent') or rule.get('smart_kind')),
                cancel_check=cancel_check, asset_root=asset_root, predecessors=batch.decisions,
                prepared_scorer=batch.scorer, progress=progress, timings=timings)
        else:
            rows = [decision_row(batch.decisions[item.uid], batch.raw_by_uid[item.uid]) for item in batch.items]
            result = {"rows": rows, "snapshot_id": batch.snapshot_id, "violations": [], "warehouse_score": None,
                      "loss_check": {"complete": True, "required_retained_uids": [], "scopes": []}}
        rows = result.get('rows')
        if (result.get('snapshot_id') != batch.snapshot_id or not isinstance(rows, list)
                or len(rows) != len(batch.items)
                or {row.get('uid') for row in rows} != set(batch.raw_by_uid)):
            raise ValueError('仓库预览结果不完整，不替换已有草稿')
        for row in rows:
            decision = batch.decisions[row['uid']]
            if decision.status != DecisionStatus.UNDECIDED:
                expected = decision_row(decision, batch.raw_by_uid[row['uid']])
                if any(row.get(key) != expected[key] for key in (
                        'predecessor_fixed', 'fixed_state_target', 'decision_status', 'decision_stage')):
                    raise ValueError('智能结果改变了前序决定，不替换已有草稿')
        result['conflicts'] = freeze_conflict_details(batch, asset_root)
        state_identity = tuple(sorted((row['kind'], row['uid_slot'], row['uid_serial'], row.get('locked'),
            row.get('discarded'), row.get('equipped')) for row in data['originals']))
        if timings is not None:
            timings.note("preview", item_count=len(batch.items), intelligent_enabled=bool(intelligent and remaining))
    if cancel_check():
        raise CancelledError()
    guard = RecommendationGuard(
        database, result["snapshot_id"], identity, files, version, stored_matching, stored_config, check_context
    )
    guard.required_retained_uids=frozenset((result.get('loss_check') or {}).get('required_retained_uids',()))
    guard.initially_discarded_uids=frozenset(r['uid'] for r in result.get('rows',()) if r.get('original_discarded'))
    guard.state_identity = state_identity
    guard.timings = timings
    guard.require_current(timings=timings)
    return result, guard
