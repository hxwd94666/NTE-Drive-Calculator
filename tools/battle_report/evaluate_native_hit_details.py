# 对照整场原生逐击详情的全部投影及渲染文本，只输出脱敏差异与传输开销。
from __future__ import annotations
import argparse
from contextlib import closing
from dataclasses import asdict
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from tools.battle_report.evaluate_native_analysis import _clone_database, compare_results  # noqa: E402
from tools.battle_report.evaluate_native_application_parts import numeric_wire  # noqa: E402
from src.services.battle_report.history.service import BattleReportHistoryService  # noqa: E402
from src.services.battle_report.persistence import BattleReportPersistenceDependencies  # noqa: E402
from src.services.battle_report.replay.preparation import BattleHitProjectionPreparationService  # noqa: E402
from src.services.battle_report.replay.formula_projection import project_formula_hit  # noqa: E402
from src.services.battle_report.damage.weave_source import BattleWeaveSourceIndex  # noqa: E402
from src.services.battle_report.replay.buff_cache import BattleHitBuffProjectionCache  # noqa: E402
from src.services.battle_report.buffs.interval_index import BattleBuffIntervalIndex  # noqa: E402
from src.services.battle_report.buffs.attribute_projection import BattleBuffAttributeProjectionService  # noqa: E402
from src.services.battle_report.buffs.hit_explanation import BattleHitBuffExplanationService  # noqa: E402
from src.services.battle_report.replay.explanation import BattleHitReplayExplanationService  # noqa: E402
from src.integrations.native_battle_page_wire import decode_page  # noqa: E402


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ("database","static","executable","output"):
        parser.add_argument(f"--{name}",type=Path,required=True)
    parser.add_argument("--record",type=int,required=True)
    parser.add_argument("--start-us",type=int)
    parser.add_argument("--end-us",type=int)
    args=parser.parse_args()
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(dir=args.output.parent,prefix="hit-details-") as temporary:
        database=Path(temporary).resolve()/"user.sqlite3"
        _clone_database(args.database.resolve(),database)
        with closing(sqlite3.connect(database.as_uri()+"?mode=ro",uri=True)) as conn:
            account=conn.execute("SELECT account_id FROM database_profile LIMIT 1").fetchone()[0]
        with closing(sqlite3.connect(args.static.resolve().as_uri()+"?mode=ro",uri=True)) as conn:
            dataset=conn.execute("SELECT dataset_id FROM dataset LIMIT 1").fetchone()[0]
        request={"schema_version":"nte-analysis-request-v1","batch_kind":"battle_page_v1","dataset_version":dataset,
                 "request":{"account_id":account,"generation":0,"battle_record_id":args.record,
                            "user_database_path":str(database),"static_database_path":str(args.static.resolve()),
                            "semantics_path":str(ROOT/"config/gameplay_effect_semantics.json"),"detail_level":"hit",
                            "start_us":args.start_us,"end_us":args.end_us}}
        process=subprocess.run([str(args.executable.resolve())],input=json.dumps(request),text=True,
                               encoding="utf-8",capture_output=True,timeout=300,check=False)
        response=json.loads(process.stdout)
        if process.returncode:
            raise RuntimeError(response.get("error",{}).get("code","native_failed"))
        page=response["result"]
        decoded=decode_page(page)
        captured=[]
        original=BattleHitProjectionPreparationService.prepare

        def capture(analysis,evidence,**kwargs):
            captured.append((analysis,evidence))
            return original(analysis,evidence,**kwargs)

        history=BattleReportHistoryService(dependencies=BattleReportPersistenceDependencies(
            account,database,0,args.static.resolve()),context_is_current=lambda _:True)
        with patch.object(BattleHitProjectionPreparationService,"prepare",side_effect=capture):
            analysis=history.load_analysis(args.record,include_buff_inference=True,
                                           include_hit_replays=True,include_buff_counterfactuals=False,
                                           start_us=args.start_us,end_us=args.end_us)
        skills={row.event_id:row for row in captured[-1][1]}
        selected={hit.event_id for hit in analysis.hits}
        replays={row.event_id:row for row in analysis.hit_replays}
        hits={hit.event_id:hit for hit in (*analysis.timeline_hits,*analysis.hits)}
        caches={True:BattleHitBuffProjectionCache(BattleBuffIntervalIndex(analysis.buff_intervals)),
                False:BattleHitBuffProjectionCache(BattleBuffIntervalIndex(analysis.timeline_buff_intervals))}
        source_indices={True:BattleWeaveSourceIndex(analysis.hits),False:BattleWeaveSourceIndex(analysis.timeline_hits)}
        interval_indices={True:{row.interval_id:row for row in analysis.buff_intervals},
                          False:{row.interval_id:row for row in analysis.timeline_buff_intervals}}
        failures=[]
        uncompressed=0
        different_roles=0
        for hit in hits.values():
            inside=hit.event_id in selected
            formula_hit=project_formula_hit(hit,skills.get(hit.event_id) if inside else None,
                                            weave_sources=source_indices[inside])
            different_roles+=formula_hit.character_id!=hit.character_id
            for formula in (False,True):
                expected=caches[inside].project(formula_hit if formula else hit)
                actual,intervals=decoded.hit_details.analysis.for_hit(hit,formula=formula)
                oracle_intervals=tuple(interval_indices[inside][row.interval_id] for row in expected.decisions)
                comparison=compare_results(numeric_wire(asdict(expected)),numeric_wire(asdict(actual)),limit=12)
                if comparison["difference_count"]:
                    failures.append({"ordinal":len(failures),"kind":"projection","formula":formula,**comparison})
                uncompressed+=len(json.dumps(asdict(actual),ensure_ascii=False,separators=(",",":")).encode())
                if formula:
                    render=lambda projection,rows:BattleHitReplayExplanationService.build(
                        hit,replays.get(hit.event_id),active_buffs=rows,projection=projection,allow_projection_fallback=False)
                else:
                    render=lambda projection,rows:BattleHitBuffExplanationService.build(
                        hit,rows,projection=projection,allow_projection_fallback=False)
                with patch.object(BattleBuffAttributeProjectionService,"project_hit",side_effect=AssertionError("render recalculated")):
                    if render(expected,oracle_intervals)!=render(actual,intervals):
                        failures.append({"kind":"render","formula":formula})
        compact=len(json.dumps(page["hit_details"],ensure_ascii=False,separators=(",",":")).encode())
        summary={"record":args.record,"start_us":args.start_us,"end_us":args.end_us,
                 "hits":len(hits),"selected_hits":len(selected),"projections":2*len(hits),"renderings":2*len(hits),
                 "different_formula_roles":different_roles,"failures":failures,
                 "compact_bytes":compact,"uncompressed_projection_bytes":uncompressed,
                 "native_detail_generation_ns":page["hit_details"]["compute_elapsed_ns"],
                 "page_bytes":len(process.stdout.encode()),"shared_modifiers":len(page["hit_details"]["modifiers"]),
                 "shared_decisions":len(page["hit_details"]["decisions"]),"shared_projections":len(page["hit_details"]["projections"])}
        args.output.write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding="utf-8")
        print(json.dumps({k:v for k,v in summary.items() if k!="failures"},ensure_ascii=False))
        print(json.dumps({"failures":len(failures)}))
        return int(bool(failures))


if __name__=="__main__":
    raise SystemExit(main())
