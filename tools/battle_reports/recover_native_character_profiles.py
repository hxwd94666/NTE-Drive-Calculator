# 在隔离库中从战报首击原始观测恢复角色修改副本，原始快照与逐击保持不变。
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.integrations.battle_report_bundle import read_battle_report_bundle, write_battle_report_bundle_atomic
from src.services.battle_report.history.transfer import BattleReportTransferService
from src.services.battle_report.inputs.scopes import scope_changed, validate_first_hit, team_character_ids
from src.services.battle_report.inputs.settlement import native_world_bonus
from src.services.native_role_profile_projection import project_native_role_profile, validate_native_cultivation
from src.storage.sqlite.user_data_dao import UserDataDao

EDIT_TABLES = ("battle_build_edit", "battle_character_build_edit",
               "battle_character_skill_edit", "battle_character_awaken_edit")


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def recover(source: Path, destination: Path, projector: Path, static_database: Path) -> dict:
    """显式离线维修入口；只接受一个已证实配置稳定的 combat 范围。"""
    if source.resolve() == destination.resolve() or destination.exists():
        raise ValueError("恢复输出必须是尚不存在的新文件")
    payload = read_battle_report_bundle(source)
    BattleReportTransferService._validate_bundle(payload)
    if len(payload["reports"]) != 1:
        raise ValueError("本恢复入口只处理单场战报")
    if digest(static_database.read_bytes()).lower() != payload["bundle"]["static_data"]["sha256"].lower():
        raise ValueError("必须使用战报冻结的同一静态 dataset")
    report = payload["reports"][0]
    tables = report["database_rows"]["tables"]
    if any(tables.get(table) for table in EDIT_TABLES):
        raise ValueError("战报已有修改副本，不能用恢复操作覆盖")
    record = json.loads(tables["battle_axis_capture"][0]["raw_record_json"])
    if record != report["nte_core"]["record"]["raw"]:
        raise ValueError("包内原始记录的两份表示不一致")
    scopes = record["calc_capture_context"]["native_scope_builds"]
    attempts = record["native_capture"]["scopeAttempts"]
    if set(scopes) != {"combat"} or set(attempts) != {"combat"}:
        raise ValueError("多半场需独立恢复，不能合并覆盖为一个角色副本")
    snapshot = scopes["combat"]["snapshot"]
    if validate_first_hit(snapshot, attempts["combat"], record) or scope_changed(snapshot, attempts["combat"], record):
        raise ValueError("原始观测不能证明属于本场首击配置或场内配置未变")
    domain = json.dumps(snapshot["domains"]["character"], ensure_ascii=False,
                        sort_keys=True, separators=(",", ":")).encode("utf-8")
    result = subprocess.run([str(projector.resolve())], input=domain, capture_output=True,
                            timeout=30, check=True)
    projection = json.loads(result.stdout)
    if projection["missing"]:
        raise ValueError("角色原始观测仍有缺项，不能完整恢复")
    patches = projection["profiles"]
    ids = [patch["character_id"] for patch in patches]
    if len(ids) != len(set(ids)) or set(ids) != team_character_ids(snapshot):
        raise ValueError("恢复角色身份与冻结队伍不一致")
    validate_native_cultivation(patches, static_database_path=static_database)
    world, evidence = native_world_bonus(snapshot, {})
    if any(row["source"] != "native_furniture_level" for row in evidence["fields"].values()):
        raise ValueError("缺少完整家具观测，不静默补用当前账号环境")
    provenance = {"rule_version": "native_profile_recovery_v1", "scope": "combat",
                  "source_bundle_sha256": digest(source.read_bytes()),
                  "capture_operation_id": tables["battle_record"][0]["capture_operation_id"],
                  "raw_summary_sha256": tables["battle_record"][0]["raw_summary_sha256"],
                  "character_domain_sha256": digest(domain),
                  "projector_sha256": digest(projector.read_bytes())}
    with tempfile.TemporaryDirectory(prefix="nte-profile-recovery-") as directory:
        with UserDataDao(Path(directory) / "recovery.sqlite3", account_id="offline_recovery") as dao:
            imported = dao.import_battle_report_transfer_rows([report["database_rows"]])
            record_id = imported["imported_battle_record_ids"][0]
            original = dao.load_battle_report_transfer_rows(record_id)
            build = dao.load_battle_build_snapshot(record_id)
            by_id = {row["character_id"]: row for row in patches}
            if set(by_id) != {row["character_id"] for row in build["characters"]}:
                raise ValueError("原始冻结角色集合与原生队伍不一致")
            profiles = []
            for character in build["characters"]:
                profile = project_native_role_profile(character["profile"],
                    by_id[character["character_id"]], persisted=False)
                profile["battle_world_bonus"] = {"AtkAdd": world["yaodao_attack_add"],
                                                "CritDamageBase": world["quantao_crit_damage"]}
                profile["native_recovery"] = {**provenance, "reference_profile": deepcopy(profile)}
                profiles.append(profile)
            dao.save_battle_build_edit(record_id, profiles)
            changed = dao.load_battle_report_transfer_rows(record_id)
            for table, rows in original["tables"].items():
                if table not in EDIT_TABLES and rows != changed["tables"][table]:
                    raise RuntimeError("恢复操作改变了非修改副本数据")
            source_id = report["database_rows"]["source_battle_record_id"]
            for table in EDIT_TABLES:
                tables[table] = deepcopy(changed["tables"][table])
                for row in tables[table]:
                    row["battle_record_id"] = source_id
            edit = dao.load_battle_build_edit(record_id)
            edit["battle_record_id"] = source_id
            for row in edit["characters"]:
                row["battle_record_id"] = source_id
            report["saved_build_edit"] = edit
    payload["bundle"]["bundle_id"] = str(uuid4())
    payload["bundle"]["exported_at_utc"] = datetime.now(timezone.utc).isoformat()
    payload["bundle"]["native_profile_recovery"] = provenance
    # 旧派生结果不声称已按恢复副本重算；导入后由正式分析组件计算。
    report["derived_analysis"] = {"persistence_kind": "recompute_after_native_profile_recovery",
                                  "model_versions": {}, "target_inference": None,
                                  "target_life_projection": None}
    report["target_context"]["automatic_inference_at_export"] = None
    payload["manifest"]["unavailable_sections"] = [row for row in payload["manifest"].get("unavailable_sections", [])
        if row.get("section") != "saved_build_edit"]
    BattleReportTransferService._validate_bundle(payload)
    def before_replace():
        if destination.exists() or digest(source.read_bytes()) != provenance["source_bundle_sha256"]:
            raise ValueError("恢复期间输入或输出文件发生变化，未写入")

    write_battle_report_bundle_atomic(destination, payload, before_replace=before_replace)
    return {"character_ids": sorted(ids), "world_bonus": world, **provenance}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--projector", type=Path, required=True,
                        help="私有采集工程的离线 project-native-character-snapshot 示例执行文件")
    parser.add_argument("--static-database", type=Path, default=ROOT / "data/game_static.sqlite3")
    args = parser.parse_args()
    result = recover(args.input, args.output, args.projector, args.static_database)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
