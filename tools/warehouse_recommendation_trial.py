# 在一致性备份的账号上进行仓库建议试算，输出本地审计清单，不连接游戏。
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.services.inventory.recommendation.warehouse_recommendation import run_warehouse_trial
from src.services.inventory.recommendation.warehouse_recommendation_preview import post_config
from src.services.inventory.recommendation.warehouse_predecessors import evaluate_predecessors
from src.services.inventory.recommendation.warehouse_calculation_inputs import freeze_warehouse_calculation_inputs
from src.services.identification.matching_settings import MatchingSettingsService
from src.domain.equipment.eligibility.management_settings import MatchingScope
from src.domain.equipment.warehouse.intelligent_rules import RULE_SCHEME_VERSION
from src.utils.logger import logger
from tools.warehouse_recommendation_report import render_report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--frozen-account", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    account = args.frozen_account.resolve(strict=True)
    out = args.output.resolve()
    # UserDataDao 可能执行本地迁移，因此仅接受忽略的 output 下离线副本。
    if not account.is_relative_to(ROOT / "output") or not out.is_relative_to(ROOT / "output"):
        parser.error("试算账号和产物必须位于本仓库 output，不接受活动账号库")
    if out.exists():
        parser.error("输出目录已存在，改用新目录以保留此前结果")
    plan = post_config(account)
    options = plan.get('recommendation', {})
    if options.get('rule_scheme_version') != RULE_SCHEME_VERSION:
        parser.error('请先在账号中生成并保存智能规则，再冻结账号进行试算')
    out.mkdir(parents=True)
    static = out / "static"
    static.mkdir()
    for name in ("game_static.sqlite3", "manifest.json"):
        shutil.copy2(ROOT / "data" / name, static / name)
    config = out / "config"
    config.mkdir()
    shutil.copy2(ROOT / "config/stats.json", config / "stats.json")
    logger.remove()  # 离线命令不保留进程启动时的控制台 sink。
    sources = [
        ROOT / "src/domain/equipment/warehouse/warehouse_recommendation.py",
        ROOT / "src/domain/equipment/warehouse/intelligent_rules.py",
        ROOT / "src/domain/inventory/post_actions.py",
        ROOT / "src/domain/equipment/warehouse/warehouse_decisions.py",
        ROOT / "src/domain/equipment/eligibility/management_settings.py",
        ROOT / "src/services/inventory/recommendation/warehouse_predecessors.py",
        ROOT / "src/services/identification/scoring_scope.py",
        ROOT / "src/services/inventory/recommendation/warehouse_recommendation.py",
        ROOT / "src/solver/blueprint_utils.py",
        ROOT / "src/optimizer/scoring.py",
        ROOT / "third_party/analysis-core/component.json",
    ]
    source_hashes = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sources}
    print("开始只读试算", flush=True)
    inputs = freeze_warehouse_calculation_inputs(account, static / 'game_static.sqlite3', config, include_profiles=True)
    batch = evaluate_predecessors(inputs, static / 'game_static.sqlite3', config, plan)
    matching = MatchingSettingsService(account, scope=MatchingScope.WAREHOUSE).load()
    result = run_warehouse_trial(
        inputs=inputs,
        static_database=static / "game_static.sqlite3",
        config_dir=config,
        preserve_rules=tuple(rule for rule in plan['preserve_rules'] if rule.get('intelligent') or rule.get('smart_kind')),
        predecessors=batch.decisions, prepared_scorer=batch.scorer, matching_settings=matching,
        progress=lambda s: print(s, flush=True),
    )
    if post_config(account) != plan or MatchingSettingsService(account, scope=MatchingScope.WAREHOUSE).load() != matching:
        raise RuntimeError('试算期间管理设置变化，结果未发布')
    result["input_hashes"] = {
        str(p.relative_to(out)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in (static / "game_static.sqlite3", static / "manifest.json", config / "stats.json")
    }
    if any(hashlib.sha256((ROOT / p).read_bytes()).hexdigest() != h for p, h in source_hashes.items()):
        raise RuntimeError("试算期间算法或评分组件发生变化，结果未发布")
    result["source_hashes"] = source_hashes
    result['rules_sha256'] = hashlib.sha256(json.dumps(plan, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    (out / "recommendations.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    previous = []
    for folder in sorted(out.parent.glob("run-v*")):
        p = folder / "recommendations.json"
        if folder != out and p.is_file():
            previous.append(json.loads(p.read_text(encoding="utf-8")))
    audit = render_report(result, out / "仓库推荐试算.html", previous)
    (out / "audit.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
    if not audit["all_passed"]:
        raise RuntimeError("试算审计存在未通过项目，请查看本地报告")
    print(
        json.dumps(
            {
                k: result[k]
                for k in [
                    "total_items",
                    "counts_by_kind",
                    "role_count",
                    "warehouse_score",
                    "elapsed_seconds",
                    "violations",
                ]
            },
            ensure_ascii=False,
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
