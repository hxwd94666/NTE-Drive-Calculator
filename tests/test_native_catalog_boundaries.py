# 验证上下半场、外部赐福与有限二次暴击桥接的原生完整输出。
from dataclasses import asdict, replace
import json
import os
import subprocess
import unittest

from tests.test_native_catalog_states import normalize
from tests.battle_report.environment.half_fixtures import _hit, _interval
from tests.battle_report.forks.test_critical_inference import _hit as critical_hit, _replay
from src.services.battle_report.environment.half_buff_scope import BattleHalfBuffScopeService
from src.services.battle_report.forks.critical_inference import BattleForkCriticalInferenceService
from src.services.battle_report.environment.conditions import battle_witch_buff_interval
from src.domain.battle_report import BattleTargetCondition


@unittest.skipUnless(os.environ.get("NTE_CATALOG_PROBE"), "independent catalog probe required")
class CatalogBoundaries(unittest.TestCase):
    def invoke(self, request):
        completed = subprocess.run([os.environ["NTE_CATALOG_PROBE"]], input=json.dumps(normalize(request)),
                                   text=True, encoding="utf-8", capture_output=True, check=True)
        return json.loads(completed.stdout)

    def test_half_scope_cross_half_unknown_roster_and_external(self):
        intervals = tuple(_interval(str(i), i) for i in (0, 1003, 1004, 1076))
        for raw in ((), (_hit(0, "upper", 1003),),
                    (_hit(0, " Upper ", 1003), _hit(20_000_000, "lower", 1004), _hit(21_000_000, "lower", 1003)),
                    (_hit(0, "lower", 1003), _hit(20_000_000, "upper", 1004, character_known=False))):
            expected = BattleHalfBuffScopeService.scope(intervals, raw_hits=raw, battle_end_us=100_000_000)
            actual = self.invoke({"operation": "half_scope", "intervals": intervals,
                                  "raw_hits": raw, "battle_end_us": 100_000_000})
            self.assertEqual(normalize(expected), actual["intervals"])

    def test_witch_unknown_and_zero_value(self):
        empty = BattleTargetCondition("public-target", 80, "unknown", 0.0, 0.0, ())
        for condition in (None, empty, replace(empty,
                witch_buff_id="public-witch", witch_buff_property_id="AtkUp", witch_buff_value=0.0)):
            expected = battle_witch_buff_interval(condition, 15_000_000)
            actual = self.invoke({"operation": "witch", "condition": condition, "battle_end_us": 15_000_000})
            self.assertEqual(normalize(asdict(expected)) if expected else None, actual["interval"])

    def test_critical_identity_missing_hit_and_duplicate_order(self):
        hits = (critical_hit("crit", 1076), critical_hit("normal", 1003), critical_hit("unknown", None))
        replays = (_replay("crit", "critical"), _replay("normal", "non_critical"),
                   _replay("unknown", "critical"), _replay("missing", "critical"),
                   replace(_replay("crit", "critical"), observed_damage=5.0))
        for rules in (None, ()):
            expected = BattleForkCriticalInferenceService.infer(hits, replays, rules)
            actual = self.invoke({"operation": "critical", "hits": hits, "replays": replays, "rules": rules})
            self.assertEqual(normalize(expected), actual["events"])
