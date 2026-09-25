# 验证原生详情共享表的原始/公式口径、引用校验及禁止界面补算。
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch
import unittest

from tests.test_battle_weave_prepared_projection import fixture
from src.integrations.native_battle_hit_details_wire import decode_hit_details
from src.integrations.nte_analysis_core import NativeAnalysisError
from src.services.battle_hit_buff_explanation_service import BattleHitBuffExplanationService
from src.services.battle_hit_replay_explanation_service import BattleHitReplayExplanationService
from src.services.battle_buff_attribute_projection_service import BattleBuffAttributeProjectionService


def tables():
    analysis, _, hit, interval = fixture()
    snapshot = SimpleNamespace(hits=analysis.hits, timeline_hits=analysis.hits,
                               buff_intervals=(interval,), timeline_buff_intervals=(interval,))
    strings = [hit.event_id,"MagBase",interval.interval_id,interval.buff_name,"高","self","applied"]
    value = {"result_encoding":"interned_v1","strings":strings,
             "modifiers":[[1,100.0,[2],[3],4,5]],"decisions":[[2,3,6,[1],[]]],
             "projections":[[0,[],[],[],[],4,[]],[0,[0],[2],[],[],4,[0]]],
             "results":[{"job_id":f"analysis:raw:{hit.event_id}","projection_index":0},
                        {"job_id":f"analysis:formula:{hit.event_id}","projection_index":1},
                        {"job_id":f"candidate:formula:{hit.event_id}","projection_index":1}]}
    return value,snapshot,hit,interval


class NativeHitDetailTests(unittest.TestCase):
    def test_projection_transport_negotiates_v2_only_with_advertised_capability(self):
        from dataclasses import asdict
        import json
        from pathlib import Path
        import tempfile
        from src.integrations.nte_analysis_core import NteAnalysisCoreClient
        value,_,hit,_=tables()
        value.update(schema_version="nte-analysis-response-v1",engine_version="0.3.0",
                     dataset_version="fixture",batch_kind="buff_projection_v1",compute_elapsed_ns=1)
        value["results"]=[{"job_id":"0","projection_index":1}]
        payload={"hits":[asdict(hit)],"intervals":[],"jobs":[
            {"job_id":"0","hit_index":0,"active_indices":[],"temporal_indices":[]}]}
        with tempfile.TemporaryDirectory() as directory:
            exe=Path(directory)/"nte-analysis-core.exe";exe.touch()
            for encoding in ("interned_v1","interned_v2"):
                caps=frozenset({"interned_buff_projection_v2"}) if encoding=="interned_v2" else frozenset()
                client=NteAnalysisCoreClient(exe,"fixture",capabilities=caps)
                response=deepcopy(value);response["result_encoding"]=encoding
                if encoding=="interned_v2":
                    response["decisions"][0].extend([3,4])
                with patch.object(client,"_run",return_value=json.dumps(response).encode()) as run:
                    result=client.project_batch(payload)
                self.assertEqual(encoding,json.loads(run.call_args.args[0])["result_encoding"])
                self.assertEqual(3 if encoding=="interned_v2" else None,
                                 result[0]["decisions"][0].get("observed_stacks"))

    def test_v2_keeps_observed_stacks_separate_from_formula_and_validates_types(self):
        from src.integrations.native_buff_projection_wire import expand_projection_tables
        from src.services.battle_native_buff_projection import projection_from_wire
        value,analysis,hit,_=tables()
        value["result_encoding"]="interned_v2"
        value["strings"].append("低")
        value["decisions"][0].extend([3,len(value["strings"])-1])
        details=decode_hit_details(value,analysis,analysis)
        formula,intervals=details.analysis.for_hit(hit,formula=True)
        decision=formula.decisions[0]
        self.assertEqual(3,decision.observed_stacks)
        self.assertEqual("低",decision.state_confidence)
        text=BattleHitBuffExplanationService.build(hit,intervals,projection=formula,allow_projection_fallback=False)
        self.assertIn("回调采样层数：3",text)
        self.assertIn(f"公式采用区间层数 ×{intervals[0].stacks}",text)
        expanded=expand_projection_tables(value)
        raw=expanded[1]["projection"]
        from src.integrations.nte_analysis_core import NteAnalysisCoreClient
        NteAnalysisCoreClient._validate_projection(raw)
        self.assertEqual(decision,projection_from_wire(raw).decisions[0])
        for invalid in (True,-1,"3"):
            broken=deepcopy(value);broken["decisions"][0][5]=invalid
            with self.assertRaises(NativeAnalysisError):
                decode_hit_details(broken,analysis,analysis)
            with self.assertRaises(NativeAnalysisError):
                NteAnalysisCoreClient._validate_projection(expand_projection_tables(broken)[1]["projection"])

    def test_raw_and_formula_views_share_tables_without_mixing_attribution(self):
        value,analysis,hit,interval=tables()
        value['future_extension'] = {'unresolved': True}
        for row in value['results']:
            row['future_evidence'] = ['unknown']
        details=decode_hit_details(value,analysis,analysis)
        raw,_=details.analysis.for_hit(hit,formula=False)
        formula,intervals=details.analysis.for_hit(hit,formula=True)
        candidate,_=details.candidate.for_hit(hit,formula=True)
        self.assertEqual((),raw.modifiers)
        self.assertEqual(100.0,formula.modifiers[0].additive_value)
        self.assertIs(formula.modifiers[0],candidate.modifiers[0])
        self.assertIs(formula.decisions[0],candidate.decisions[0])
        self.assertIs(interval,intervals[0])
        with patch.object(BattleBuffAttributeProjectionService,"project_hit",side_effect=AssertionError("recalculated")):
            text=BattleHitBuffExplanationService.build(hit,intervals,projection=formula,allow_projection_fallback=False)
        self.assertIn("100",text)

    def test_missing_or_stale_hit_never_borrows_another_projection(self):
        value,analysis,hit,_=tables()
        details=decode_hit_details(value,analysis,None)
        self.assertEqual((None,()),details.analysis.for_hit(replace(hit,relative_time_us=8),formula=True))
        self.assertEqual((None,()),details.analysis.for_hit(replace(hit,event_id="unknown"),formula=True))

    def test_invalid_references_nonfinite_values_and_duplicate_jobs_are_rejected(self):
        value,analysis,_,_=tables()
        cases=[]
        for index in (-1,True,100):
            broken=deepcopy(value);broken["results"][0]["projection_index"]=index;cases.append(broken)
        broken=deepcopy(value);broken["modifiers"][0][1]=float("nan");cases.append(broken)
        broken=deepcopy(value);broken["results"].append(broken["results"][0]);cases.append(broken)
        broken=deepcopy(value);broken["decisions"][0][2]=1;cases.append(broken)
        broken=deepcopy(value);broken["strings"]="invalid table";cases.append(broken)
        for broken in cases:
            with self.assertRaises(NativeAnalysisError):
                decode_hit_details(broken,analysis,analysis)

    def test_missing_native_details_render_explicit_gap_without_rules(self):
        _,_,hit,_=tables()
        with patch.object(BattleBuffAttributeProjectionService,"project_hit",side_effect=AssertionError("recalculated")):
            text=BattleHitBuffExplanationService.build(hit,(),allow_projection_fallback=False)
            formula=BattleHitReplayExplanationService.build(hit,None,allow_projection_fallback=False)
        self.assertIn("未生成 Buff 分析详情",text)
        self.assertIn("没有重放结果",formula)
