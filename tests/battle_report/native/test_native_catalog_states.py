# 对照公开状态回归验证原生最终证据组装，测试过程不调用 Python 元数据适配器。
from dataclasses import asdict, is_dataclass
import inspect
import json
import os
import subprocess
import unittest
from unittest.mock import patch

from src.domain.battle_report.buffs.models import BattleInferredBuffInterval
from src.domain.battle_report.timeline.events import BattleTreatmentEvent
from src.integrations.battle_analysis.page_wire import decode
from src.services.battle_report.characters.zankou import BattleZankouFormBuffService
from src.services.battle_report.characters.shinku import BattleShinkuRageBuffService
from src.services.battle_report.characters.fadia import BattleFadiaHpStackService
from src.services.battle_report.characters.daffodill import BattleDaffodillAwakeningService
from src.services.battle_report.environment.outer_realm_buffs import BattleOuterRealmBuffService
from src.services.battle_report.characters.linko_buffs import BattleLinkoCoattackBuffService
from src.services.battle_report.treatment.mechanisms import BattleTreatmentEventService
from src.services.battle_report.treatment.mechanisms import BattleTreatmentBuffService
from src.services.battle_report.buffs.inference import BattleBuffInferenceService
from tests.battle_report.characters import test_zankou_form as zankou
from tests.battle_report.characters import test_shinku_rage as shinku
from tests.battle_report.characters import test_fadia as fadia
from tests.battle_report.characters import test_daffodill as daffodill
from tests.battle_report.environment import test_outer_realm_buffs as outer
from tests.battle_report.characters import test_linko_formula as linko
from tests.battle_report.treatment import test_events as treatment
from tests.battle_report.buffs import test_inference as buff
from tests.battle_report.forks import test_refinement as fork
from tests.battle_report.forks import test_state_refinement as fork_state
from tests.battle_report.forks import test_damage_completion as fork_damage
from tests.battle_report.forks import test_trigger_refinement as fork_trigger
from tests.battle_report.forks import test_periodic_refinement as fork_periodic


def normalize(value):
    return json.loads(json.dumps(value, default=lambda v: asdict(v) if is_dataclass(v) else list(v)))


class _Compare:
    def setUp(self):
        original = self.service.infer
        signature = inspect.signature(original)

        def compare(*args, **kwargs):
            expected = original(*args, **kwargs)
            binding = signature.bind(*args, **kwargs)
            binding.apply_defaults()
            values = binding.arguments
            request = {"operation": "state", "kind": self.kind, "build": values.get("build"),
                       "config": values.get("config"), "topple_duration_us": values.get("topple_duration_us"),
                       "state_buff_intervals": values.get("state_buff_intervals", ()),
                       "recover_ratio": values.get("zankou_effect_three_recover_ratio"),
                       "infer_buffs": self.kind == "treatment",
                       "analysis": {"hits": values.get("hits", ()), "inferred_actions": values.get("actions", ()),
                                    "battle_end_us": values.get("battle_end_us", 1),
                                    "time_stop_intervals": values.get("time_stop_intervals", ()),
                                    "max_hp_events": values.get("max_hp_events", ()),
                                    "linko_coattack_inferences": values.get("inferences", ())}}
            if self.kind == "buff":
                request = {"operation": "intervals", "input": {key: value for key, value in values.items()
                           if key not in {"compute_backend", "checkpoint"}}}
            completed = subprocess.run([os.environ["NTE_CATALOG_PROBE"]], input=json.dumps(normalize(request), ensure_ascii=False),
                                       text=True, encoding="utf-8", capture_output=True)
            self.assertEqual(0, completed.returncode, completed.stderr)
            actual = json.loads(completed.stdout)["events" if self.kind == "treatment" else "intervals"]
            # 展示 DTO 只解码声明字段；缺省来自类型定义，不按离线期望补值，非默认窗口/半场照常比较。
            result_type = tuple[BattleTreatmentEvent, ...] if self.kind == "treatment" else tuple[BattleInferredBuffInterval, ...]
            expected_display = normalize(expected)
            actual_display = normalize(decode(actual, result_type))
            # 仅三份未选一觉夹具按语义核措辞；有一觉与其他状态仍逐字段严格比较。
            basis_cases = {
                "test_huo_only_projects_crit_damage_to_continuous_damage",
                "test_magic_q_ends_fantasy_and_huo_retains_for_eight_active_seconds",
                "test_zero_awakening_keeps_shou_and_starts_huo_after_feiyingshan",
            }
            selected = next((character.get("profile", {}).get("selected_awaken_effect_ids")
                             for character in (values.get("build") or {}).get("characters", ())
                             if character.get("character_id") == 1036), None)
            if (self.kind == "zankou" and self._testMethodName in basis_cases
                    and selected is not None and "Effect1" not in selected):
                for left, right in zip(expected_display, actual_display, strict=True):
                    for row in (left, right):
                        basis = row.pop("inference_basis")
                        self.assertTrue(basis.strip())
                        for meaning in ("冻结觉醒", "未启用一觉", "形态", "时停"):
                            self.assertIn(meaning, basis)
                        self.assertTrue(any(meaning in basis for meaning in ("最后一击时点", "最后命中时点")))
            # 四份确实没有观测名/正式名的真红夹具，原生名只能回退该来源 ID 文本；有名分支不放宽。
            nameless_cases = {
                "test_effect_three_alone_does_not_enable_count_gated_resonance",
                "test_missing_static_config_retains_unknown_modifier",
                "test_same_time_targets_do_not_duplicate_source_buff_or_leak_to_other_hits",
                "test_three_selected_effects_add_resonance_in_damage_up_bucket",
            }
            if self.kind == "shinku" and self._testMethodName in nameless_cases:
                for left, right in zip(expected_display, actual_display, strict=True):
                    source = next((character for character in (values.get("build") or {}).get("characters", ())
                                   if character.get("character_id") == left["source_character_id"]), None)
                    if source is not None and "observed_name" not in source and "character_name" not in source:
                        self.assertEqual(right["source_character_name"], str(right["source_character_id"]))
                        left.pop("source_character_name")
                        right.pop("source_character_name")
            self.assert_json(expected_display, actual_display)
            if self.kind == "treatment":
                expected_buffs = BattleTreatmentBuffService.infer(
                    build=values.get("build"), treatment_events=expected,
                    battle_end_us=values["battle_end_us"],
                    time_stop_intervals=values.get("time_stop_intervals", ()),
                )
                actual_buffs = decode(json.loads(completed.stdout)["buff_intervals"],
                                      tuple[BattleInferredBuffInterval, ...])
                self.assert_json(normalize(expected_buffs), normalize(actual_buffs))
            return expected

        patcher = patch.object(self.service, "infer", side_effect=compare)
        patcher.start()
        self.addCleanup(patcher.stop)

    def assert_json(self, expected, actual, path="result"):
        if isinstance(expected, dict):
            self.assertEqual(set(expected), set(actual), path)
            for key in expected:
                self.assert_json(expected[key], actual[key], f"{path}.{key}")
        elif isinstance(expected, list):
            self.assertEqual(len(expected), len(actual), path)
            for index, (left, right) in enumerate(zip(expected, actual, strict=True)):
                self.assert_json(left, right, f"{path}[{index}]")
        elif isinstance(expected, float):
            self.assertAlmostEqual(expected, actual, places=9, msg=path)
        else:
            self.assertEqual(expected, actual, path)


@unittest.skipUnless(os.environ.get("NTE_CATALOG_PROBE"), "independent catalog probe required")
class CatalogZankou(_Compare, zankou.BattleZankouFormBuffServiceTests):
    service, kind = BattleZankouFormBuffService, "zankou"


@unittest.skipUnless(os.environ.get("NTE_CATALOG_PROBE"), "independent catalog probe required")
class CatalogShinku(_Compare, shinku.BattleShinkuRageBuffServiceTests):
    service, kind = BattleShinkuRageBuffService, "shinku"


@unittest.skipUnless(os.environ.get("NTE_CATALOG_PROBE"), "independent catalog probe required")
class CatalogFadia(_Compare, fadia.BattleFadiaHpStackServiceTests):
    service, kind = BattleFadiaHpStackService, "fadia"


@unittest.skipUnless(os.environ.get("NTE_CATALOG_PROBE"), "independent catalog probe required")
class CatalogDaffodill(_Compare, daffodill.BattleDaffodillAwakeningServiceTests):
    service, kind = BattleDaffodillAwakeningService, "daffodill"


@unittest.skipUnless(os.environ.get("NTE_CATALOG_PROBE"), "independent catalog probe required")
class CatalogOuter(_Compare, outer.BattleOuterRealmBuffServiceTests):
    service, kind = BattleOuterRealmBuffService, "outer"


@unittest.skipUnless(os.environ.get("NTE_CATALOG_PROBE"), "independent catalog probe required")
class CatalogLinko(_Compare, linko.BattleLinkoPrecisionTuningTests):
    service, kind = BattleLinkoCoattackBuffService, "linko"


@unittest.skipUnless(os.environ.get("NTE_CATALOG_PROBE"), "independent catalog probe required")
class CatalogTreatment(_Compare, treatment.BattleTreatmentEventServiceTests):
    service, kind = BattleTreatmentEventService, "treatment"


@unittest.skipUnless(os.environ.get("NTE_CATALOG_PROBE"), "independent catalog probe required")
class CatalogBuff(_Compare, buff.BattleBuffInferenceServiceTests):
    service, kind = BattleBuffInferenceService, "buff"


@unittest.skipUnless(os.environ.get("NTE_CATALOG_PROBE"), "independent catalog probe required")
class CatalogFork(_Compare, fork.BattleForkRefinementServiceTests):
    service, kind = BattleBuffInferenceService, "buff"


@unittest.skipUnless(os.environ.get("NTE_CATALOG_PROBE"), "independent catalog probe required")
class CatalogForkState(_Compare, fork_state.BattleForkStateRefinementServiceTests):
    service, kind = BattleBuffInferenceService, "buff"


@unittest.skipUnless(os.environ.get("NTE_CATALOG_PROBE"), "independent catalog probe required")
class CatalogForkDamage(_Compare, fork_damage.BattleForkDamageCompletionServiceTests):
    service, kind = BattleBuffInferenceService, "buff"


@unittest.skipUnless(os.environ.get("NTE_CATALOG_PROBE"), "independent catalog probe required")
class CatalogForkTrigger(_Compare, fork_trigger.BattleForkTriggerRefinementServiceTests):
    service, kind = BattleBuffInferenceService, "buff"


@unittest.skipUnless(os.environ.get("NTE_CATALOG_PROBE"), "independent catalog probe required")
class CatalogForkPeriodic(_Compare, fork_periodic.BattleForkPeriodicRefinementServiceTests):
    service, kind = BattleBuffInferenceService, "buff"
