# 对照公开创生被动反事实测试与生命周期能力矩阵验证原生完整结果。
from dataclasses import replace
import inspect
import json
import os
import subprocess
import unittest
from unittest.mock import patch

from tests.battle_report.creation import test_passives as public
from tests.test_native_catalog_states import normalize, _Compare
from src.services.battle_report.creation.counterfactual import BattleCreationPassiveCounterfactualService
from src.services.battle_report.creation.evaluation import (
    BattleCreationPassiveEvaluationService, BattleCreationPassiveEvidence, BattleCreationPassiveAttribution,
)


class _CreationCompare(_Compare):
    def setUp(self):
        original = self.service.calculate
        signature = inspect.signature(original)

        def compare(*args, **kwargs):
            expected = original(*args, **kwargs)
            bound = signature.bind(*args, **kwargs)
            bound.apply_defaults()
            request = {"operation": self.operation, **bound.arguments}
            result = subprocess.run([os.environ["NTE_CATALOG_PROBE"]], input=json.dumps(normalize(request)),
                                    text=True, encoding="utf-8", capture_output=True)
            self.assertEqual(0, result.returncode, result.stderr)
            self.assert_json(normalize(expected), json.loads(result.stdout)["rows"])
            return expected

        patcher = patch.object(self.service, "calculate", side_effect=compare)
        patcher.start()
        self.addCleanup(patcher.stop)


@unittest.skipUnless(os.environ.get("NTE_CATALOG_PROBE"), "independent catalog probe required")
class CatalogCreationExplicit(_CreationCompare, public.BattleCreationPassiveCounterfactualServiceTests):
    service, operation = BattleCreationPassiveCounterfactualService, "creation_explicit"


@unittest.skipUnless(os.environ.get("NTE_CATALOG_PROBE"), "independent catalog probe required")
class CatalogCreationEvaluation(_CreationCompare, public.BattleCreationPassiveEvaluationServiceTests):
    service, operation = BattleCreationPassiveEvaluationService, "creation_evaluation"

    def test_lifecycle_capability_attribution_and_unknown_owner_matrix(self):
        hits = (public._hit("1:formal", 80.0, character_id=1010, character_name="公开角色",
                            gameplay_effect_id="GE_ActorReaction_1_Damage"),
                public._hit("2:label", 50.0, character_id=1055, character_name="公开队友"),
                public._hit("3:unknown", 40.0, character_id=0, character_name="",
                            gameplay_effect_id="GE_ActorReaction_1_1019_Damage"))
        build = {"characters": [public._character(cid, str(cid)) for cid in (1010, 1019, 1055, 1052, 1021)]}
        for complete in (False, True):
            for event_mode in (0, 1, 2):
                analysis = public._snapshot(hits if event_mode else (), axis_complete=complete,
                                            time_stop_intervals=((2_000_000, 3_000_000),) if event_mode == 2 else ())
                for flags in (False, True):
                    evidence = BattleCreationPassiveEvidence(
                        single_target_confirmed=flags, time_stop_axis_complete=flags,
                        target_positions_complete=flags, plant_identity_complete=flags,
                        volley_identity_complete=flags, lifecycle_complete=flags,
                        creation_cap_order_complete=flags, future_action_axis_complete=flags,
                        edgar_trigger_axis_complete=flags,
                    )
                    self.service.calculate(analysis, build, evidence=evidence)
                    if event_mode:
                        for attribution_complete in (False, True):
                            for ids in ((), ("1:formal",), ("1:formal", "2:label", "3:unknown")):
                                attributions = tuple(BattleCreationPassiveAttribution(
                                    adapter, ids, attribution_complete, "公开生命周期证据",
                                ) for adapter in ("creation-volley", "creation-radius", "creation-cap", "creation-time-stop"))
                                self.service.calculate(analysis, build, evidence=replace(evidence, attributions=attributions))
