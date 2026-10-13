# 将公共分析领域默认值和版本声明导出为原生核心构建输入，不读取账号数据。
from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.services.battle_report.build.offline_analysis import BattleCounterfactualAnalysisService  # noqa: E402
from src.services.battle_report.buffs.inference import BUFF_INFERENCE_MODEL_VERSION  # noqa: E402
from src.services.battle_report.buffs.attribute_projection import BUFF_ATTRIBUTE_PROJECTION_VERSION  # noqa: E402
from src.services.battle_report.characters.linko_buffs import LINKO_COATTACK_BUFF_MODEL_VERSION  # noqa: E402
from src.services.battle_report.environment.outer_realm_buffs import OUTER_REALM_BUFF_MODEL_VERSION  # noqa: E402
from src.services.battle_report.replay.service import HIT_REPLAY_MODEL_VERSION  # noqa: E402
from src.services.battle_report.buffs.counterfactual import BUFF_COUNTERFACTUAL_MODEL_VERSION  # noqa: E402
from src.services.battle_report.damage.passive_counterfactual import PASSIVE_COUNTERFACTUAL_MODEL_VERSION  # noqa: E402
from src.storage.sqlite.static_game_data_metadata import MINIMUM_SUPPORTED_SCHEMA_VERSION, SCHEMA_VERSION  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    empty = BattleCounterfactualAnalysisService.analyze(
        battle_record_id=1, evidence=None, build=None, capability_level="summary_only", infer_buffs=False,
    )
    payload = {
        "analysis_defaults": asdict(empty),
        "static_schema_range": [MINIMUM_SUPPORTED_SCHEMA_VERSION, SCHEMA_VERSION],
        "versions": {
            "buff_inference_version": BUFF_INFERENCE_MODEL_VERSION,
            "buff_attribute_projection_version": BUFF_ATTRIBUTE_PROJECTION_VERSION,
            "linko_coattack_buff_model_version": LINKO_COATTACK_BUFF_MODEL_VERSION,
            "outer_realm_buff_model_version": OUTER_REALM_BUFF_MODEL_VERSION,
            "hit_replay_model_version": HIT_REPLAY_MODEL_VERSION,
            "buff_counterfactual_model_version": BUFF_COUNTERFACTUAL_MODEL_VERSION,
            "passive_counterfactual_model_version": PASSIVE_COUNTERFACTUAL_MODEL_VERSION,
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
