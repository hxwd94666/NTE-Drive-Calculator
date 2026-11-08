# 一致性冻结推荐输入，预览只产出待保存状态；真正写入仍经仓库后验确认流程。
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory

from src.domain.post_actions import merge_post_action_config
from src.domain.equipment.intelligent_rules import RULE_SCHEME_VERSION
from src.domain.equipment.warehouse_targets import recommendation_targets
from src.services.accounts.settings import AccountSettingsService
from src.services.identification.matching_settings import MatchingSettingsService
from src.services.inventory.warehouse_recommendation import run_warehouse_trial
from src.storage.sqlite.warehouse_input_snapshot import freeze_warehouse_inputs
from src.storage.sqlite.warehouse_recommendation_inputs import recommendation_account_identity
from src.storage.sqlite.user_data_dao import UserDataDao


def file_identity(paths):
    return tuple((str(p.resolve()), p.stat().st_size, p.stat().st_mtime_ns) for p in paths)


def post_config(path):
    return merge_post_action_config(AccountSettingsService(path).load("ui").get("full_scan_post_action_config"))


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

    def require_targets(self, targets):
        if not any(state=='discarded' for state in targets.values()):
            return
        missing=[uid for uid in self.required_retained_uids
                 if targets.get(uid,'discarded' if uid in self.initially_discarded_uids else 'normal')=='discarded']
        if missing:
            raise ValueError(f'有 {len(missing)} 件已验证配装所需装备未保留。请同时采用所需恢复建议，或重新计算后再弃置。')

    def require_current(self, *, check_snapshot=True):
        self.check_context()
        if (
            recommendation_account_identity(self.database) != self.account_identity
            or file_identity(self.files) != self.file_version
            or MatchingSettingsService(self.database).load() != self.matching
            or post_config(self.database) != self.config
        ):
            raise ValueError("仓库建议的配装、权重、资料或规则已变化，请重新生成建议")
        if check_snapshot:
            with UserDataDao(self.database) as dao:
                if dao.current_inventory_snapshot_id() != self.snapshot_id:
                    raise ValueError("背包快照已更新，请重新生成仓库建议")

    def bind_saved_settings(self, config, matching):
        # 只承认这次编辑器提交的确切设置，不重读最新值冒充旧输入。
        self.config = merge_post_action_config(config)
        self.matching = deepcopy(matching)
        self.require_current()


def preview_targets(result):
    if result.get("violations"):
        if (result.get('loss_check') or {}).get('blocked_uids'):
            raise ValueError('全角色配装仍有已知损失，部分所需装备与明确弃置规则或状态保护冲突；请检查规则顺序后重算。')
        raise ValueError("整批校验未通过，不能采用建议")
    return recommendation_targets(result['rows'])


def build_preview(
    *, database, static_database, asset_root, config_dir, config, matching, options, check_context, cancel_check
):
    database, static_database, config_dir = map(Path, (database, static_database, config_dir))
    files = (static_database, static_database.with_name("manifest.json"), config_dir / "stats.json")
    # 评分库与公共图片可以分属不同目录；路径由组合根选定，不从数据库位置推算。
    asset_root = Path(asset_root)
    asset_manifest = asset_root / 'manifest.json'
    if asset_manifest.is_file():
        files += (asset_manifest,)
    version = file_identity(files)
    check_context()
    identity = recommendation_account_identity(database)
    stored_matching = MatchingSettingsService(database).load()
    stored_config = post_config(database)
    if config.get('recommendation', {}).get('rule_scheme_version') != RULE_SCHEME_VERSION:
        raise ValueError('推荐已改为全角色参与，请重新生成智能规则并保存后再计算。人工规则会保留。')
    if merge_post_action_config(config) != stored_config:
        raise ValueError('规则有未保存修改，请先保存后再计算建议。')
    with TemporaryDirectory(prefix="nte-warehouse-") as folder:
        root = Path(folder)
        frozen = root / "account.sqlite3"
        freeze_warehouse_inputs(database, frozen, cancel_check=cancel_check)
        if recommendation_account_identity(frozen) != identity:
            raise ValueError("冻结期间账号数据变化，请重新生成建议")
        (root / "data").mkdir()
        (root / "config").mkdir()
        for path in files[:2]:
            shutil.copy2(path, root / "data" / path.name)
        shutil.copy2(files[2], root / "config" / "stats.json")
        result = run_warehouse_trial(
            account_snapshot=frozen,
            static_database=root / "data" / static_database.name,
            config_dir=root / "config",
            matching_settings=deepcopy(matching),
            preserve_rules=deepcopy(config["preserve_rules"]),
            cancel_check=cancel_check,
            asset_root=asset_root,
        )
    if cancel_check():
        raise ValueError("已取消仓库建议")
    guard = RecommendationGuard(
        database, result["snapshot_id"], identity, files, version, stored_matching, stored_config, check_context
    )
    guard.required_retained_uids=frozenset((result.get('loss_check') or {}).get('required_retained_uids',()))
    guard.initially_discarded_uids=frozenset(r['uid'] for r in result.get('rows',()) if r.get('original_discarded'))
    guard.require_current()
    return result, guard
