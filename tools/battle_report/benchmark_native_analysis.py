# 用冻结源码和数据库在独立进程中顺序测量完整战报及真实边际页面服务链。
from __future__ import annotations

import argparse
from contextlib import closing
from dataclasses import fields, is_dataclass
import hashlib
import json
import os
from pathlib import Path
import pickle
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import time
import traceback
from types import SimpleNamespace


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _backup(source: Path, destination: Path) -> None:
    with closing(sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True)) as source_db:
        with closing(sqlite3.connect(destination)) as target_db:
            source_db.backup(target_db)


def _peak_working_set_bytes() -> int | None:
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes

    class ProcessMemoryCounters(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
            ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    psapi.GetProcessMemoryInfo.argtypes = [
        wintypes.HANDLE, ctypes.POINTER(ProcessMemoryCounters), wintypes.DWORD,
    ]
    psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
    counters = ProcessMemoryCounters()
    counters.cb = ctypes.sizeof(counters)
    if not psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
        return None
    return int(counters.PeakWorkingSetSize)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="以独立解释器顺序测量冻结战报与完整边际服务。")
    parser.add_argument("--source-root", required=True, type=Path)
    parser.add_argument("--user-database", required=True, type=Path)
    parser.add_argument("--static-database", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--cases", default="history:68:original:3")
    parser.add_argument("--backend", choices=("python", "native", "database"), default="python")
    parser.add_argument("--executable", type=Path)
    parser.add_argument("--reference-dir", type=Path)
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--case", default="")
    parser.add_argument("--run", type=int, default=1)
    return parser


def _marginal(history, record_id, static_database, source_root, progress, metrics):
    from src.domain.stat_catalog import StatCatalog
    from src.features.battle_report.marginal.page import BattleMarginalPage
    from src.services.battle_report.marginal.calculation import BattleMarginalCalculationService
    from src.services.battle_report.marginal.calculation_support import drive_substat_marginal_units
    from src.services.battle_report.marginal.candidates import BattleMarginalCandidateService
    from src.services.battle_report.analysis.load import (
        BattleReportAnalysisLoadRequest, BattleReportAnalysisLoadService,
    )
    worker_panel = "marginal_drive_units" in BattleReportAnalysisLoadRequest.__dataclass_fields__
    if worker_panel:
        from src.services.battle_report.marginal.panel import character_panel_marginal_units
    else:
        # The frozen v0.1/v2 application calculated its panel in the UI helper.
        from src.features.battle_report.marginal.character_panel import character_panel_marginal_units

    started = time.perf_counter()
    editor_data = history.load_build_editor_data(record_id)
    editable = bool(editor_data.get("equipment_editable", editor_data.get("marginal_equipment_editable", True)))
    prepared = BattleMarginalCandidateService.prepare_editor_data(editor_data, equipment_editable=editable)
    details = prepared["details"]
    # Exercise the actual page's public export method for lazy, unchanged editors.
    proxy = SimpleNamespace(
        _details=details, _editors=[None] * len(details),
        _editor_character_ids=[int(detail["character"]["character_id"]) for detail in details],
    )
    profiles = BattleMarginalPage.profiles(proxy)
    candidate = BattleMarginalCandidateService.freeze(record_id, profiles, equipment_editable=editable)
    selected = next((detail for detail in details if int(detail["character"]["character_id"]) == 1003), None)
    if selected is None:
        selected = next((detail for detail in details if int(detail["character"]["character_id"]) > 0), None)
    if selected is None:
        raise ValueError("frozen report does not contain an editable formal character")
    character_id = int(selected["character"]["character_id"])
    scope = selected.get("analysis_detail_scope")
    metrics["analysis_detail_scope"] = str(scope) if scope in {"first", "second"} else None
    catalog = StatCatalog.from_config_dir(source_root / "config")
    drive_units = drive_substat_marginal_units(catalog.gold_base_values)
    units = character_panel_marginal_units(drive_units)
    request = BattleReportAnalysisLoadRequest(
        battle_record_id=record_id, detail_level="marginal",
        detail_scope=str(scope) if scope in {"first", "second"} else None,
        marginal_benefit_candidate=candidate, selected_character_id=character_id,
        static_database_path=static_database,
        **({"marginal_drive_units": tuple(drive_units.items())} if worker_panel else {}),
    )
    metrics["editor_preparation_seconds"] = time.perf_counter() - started
    started = time.perf_counter()
    panel_started = None

    def load_progress(event):
        nonlocal panel_started
        if event.phase == "marginal_panel":
            if panel_started is None:
                panel_started = time.perf_counter()
            else:
                metrics["marginal_panel_seconds"] = time.perf_counter() - panel_started
        progress(event)

    loaded = BattleReportAnalysisLoadService.load(history, request, progress_callback=load_progress)
    metrics["marginal_page_load_seconds"] = time.perf_counter() - started
    if loaded.analysis is None or loaded.marginal_benefits is None:
        raise ValueError("marginal page must include analysis and equipment benefits")
    if worker_panel:
        if (loaded.marginal_panel is None or loaded.marginal_panel.character_id != character_id
                or loaded.marginal_panel.drive_property_ids != tuple(drive_units)):
            raise ValueError("worker panel does not match the frozen selected character and units")
        panel = loaded.marginal_panel.results
    else:
        started = time.perf_counter()
        panel = BattleMarginalCalculationService.calculate(
            analysis=loaded.analysis, character_id=character_id, edited_values={}, units=units,
        )
        metrics["marginal_panel_seconds"] = time.perf_counter() - started
    metrics["marginal_page_load_includes_panel"] = worker_panel
    metrics["selected_character_id"] = character_id
    metrics["unit_count"] = len(units)
    metrics["core_main_stat_candidates"] = len(loaded.marginal_benefits.core_main_stats)
    metrics["fork_benefit_present"] = loaded.marginal_benefits.fork is not None
    metrics["analysis_range_start_us"] = loaded.analysis.range_start_us
    metrics["analysis_range_end_us"] = loaded.analysis.range_end_us
    metrics["analysis_hit_count"] = len(loaded.analysis.hits)
    metrics["native_hit_details_present"] = getattr(loaded, 'hit_details', None) is not None
    return {"page": loaded, "panel_results": panel, "units": units}


def _canonical_payload(payload):
    """Keep every original page field and compare the relocated panel exactly once."""
    if isinstance(payload, dict) and "page" in payload and "panel_results" in payload:
        page = payload["page"]
        values = ({field.name: getattr(page, field.name) for field in fields(page)
                   if field.name not in {"marginal_panel", "hit_details"}
                   and (field.name != 'candidate_display_analysis' or hasattr(page, field.name))}
                  if is_dataclass(page) else dict(page))
        # The old page generated click explanations on demand. Their new shared
        # lookup tables have a separate per-hit projection/render differential;
        # they are not part of this historical calculation-output comparison.
        values.pop('hit_details', None)
        if 'candidate_display_analysis' not in values:
            # The frozen application calculated this display in the UI. Compare
            # it too, outside the timing interval, when upgrading old artifacts.
            analysis = values['analysis']
            display = None
            if getattr(analysis, 'build_counterfactual', None) is not None:
                from src.services.battle_report.build.timeline_projection import BattleBuildTimelineProjectionService
                display = BattleBuildTimelineProjectionService.project(analysis, analysis.build_counterfactual)
            values['candidate_display_analysis'] = display
        return {**payload, "page": values}
    return payload


def _comparison_payload(value):
    """Normalize JSON number spelling and unordered zero-unit panel extras only."""
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: _comparison_payload(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, dict):
        result = {key: _comparison_payload(item) for key, item in value.items()}
        if 'panel_results' in result:
            rows = result['panel_results']
            # Python appends these from a set. Keep every field and preserve
            # the order of measured, nonzero-unit properties.
            start = 0
            while start < len(rows):
                if rows[start]['unit'] != 0:
                    start += 1
                    continue
                end = start + 1
                while end < len(rows) and rows[end]['unit'] == 0:
                    end += 1
                rows[start:end] = sorted(rows[start:end], key=lambda row: row['property_id'])
                start = end
        return result
    if isinstance(value, (tuple, list)):
        return [_comparison_payload(item) for item in value]
    if type(value) is int and abs(value) <= 2 ** 53:
        return float(value)
    return value


def _worker(args) -> int:
    source_root = args.source_root.resolve()
    sys.path.insert(0, str(source_root))
    os.chdir(source_root)
    import src.services.battle_report.history.service as history_module
    from src.services.battle_report.persistence import BattleReportPersistenceDependencies
    from tools.battle_report.evaluate_native_analysis import compare_results

    if not Path(history_module.__file__).resolve().is_relative_to(source_root):
        raise RuntimeError("benchmark imported a different source tree")
    workflow, record, mode = args.case.split(":")
    record_id = int(record)
    output = args.output_dir.resolve() / args.case.replace(":", "-")
    output.mkdir(parents=True, exist_ok=True)
    metrics = {
        "case": args.case, "run": args.run, "backend": args.backend,
        "hash_seed": os.environ.get("PYTHONHASHSEED"),
        "source_module_sha256": _sha256(Path(history_module.__file__)),
        "user_snapshot_sha256": _sha256(args.user_database),
        "static_snapshot_sha256": _sha256(args.static_database),
        "execution_scope": (
            "independent process; database clone and history/backend imports excluded from measured seconds; "
            "marginal includes editor data preparation, production load service, equipment "
            "benefits, lazy page-helper imports and actual character-panel unit set; Qt widget construction/paint excluded"
        ),
    }
    last_notice = time.perf_counter()

    def progress(event):
        nonlocal last_notice
        now = time.perf_counter()
        if now - last_notice > 30:
            print(f"progress case={args.case} run={args.run} phase={event.phase}", flush=True)
            last_notice = now

    client = None
    try:
        with tempfile.TemporaryDirectory(prefix="native-performance-") as directory:
            database = Path(directory) / "user.sqlite3"
            _backup(args.user_database, database)
            with closing(sqlite3.connect(database)) as connection:
                account = connection.execute("SELECT account_id FROM database_profile LIMIT 1").fetchone()[0]
            kwargs = {}
            if args.backend in {"native", "database"}:
                from src.integrations.nte_analysis_core import NteAnalysisCoreClient
                executable = args.executable or source_root / "third_party/analysis-core/bin/nte-analysis-core.exe"
                # Frozen source baselines predate identity discovery; their paired
                # executable still uses the original constructor contract.
                bind_client = getattr(NteAnalysisCoreClient, "from_executable", NteAnalysisCoreClient)
                dataset_version = f"sha256:{_sha256(args.static_database)}"
                if args.backend == "database":
                    with closing(sqlite3.connect(args.static_database.as_uri() + "?mode=ro", uri=True)) as static_conn:
                        dataset_version = static_conn.execute("SELECT dataset_id FROM dataset").fetchone()[0]
                client = bind_client(executable=executable, dataset_version=dataset_version)
                kwargs["direct_formula_backend"] = client
                metrics["executable_sha256"] = _sha256(executable)
            dependencies = BattleReportPersistenceDependencies(
                account_id=account, user_database_path=database, generation=0,
                static_database_path=args.static_database,
            )
            if args.backend == "database":
                from src.services.battle_report.analysis.native_page import BattleNativePageService
                kwargs["native_page_loader"] = BattleNativePageService(
                    client=client, dependencies=dependencies,
                    semantics_path=source_root / "config/gameplay_effect_semantics.json",
                    context_is_current=lambda _: True,
                )
            history = history_module.BattleReportHistoryService(
                dependencies=dependencies, context_is_current=lambda _: True, **kwargs,
            )
            started = time.perf_counter()
            if workflow == "history":
                payload = history.load_analysis(
                    record_id, use_build_edit=mode == "effective", include_buff_inference=True,
                    include_hit_replays=True, include_buff_counterfactuals=True,
                    progress_callback=progress,
                )
                if payload is None:
                    raise ValueError("report analysis missing")
            elif workflow == "marginal":
                if mode != "effective":
                    raise ValueError("marginal entry uses the actual effective saved build")
                payload = _marginal(history, record_id, args.static_database, source_root, progress, metrics)
            else:
                raise ValueError("unsupported benchmark workflow")
            metrics["seconds"] = time.perf_counter() - started
            metrics["python_peak_working_set_bytes_at_calculation_end"] = _peak_working_set_bytes()
            payload = _canonical_payload(payload)
            snapshot = output / f"run-{args.run}.pickle"
            with snapshot.open("wb") as stream:
                pickle.dump(payload, stream, protocol=pickle.HIGHEST_PROTOCOL)
            metrics["snapshot_sha256"] = _sha256(snapshot)
            metrics["native_stats"] = {} if client is None else dict(client.stats)
            references = []
            if args.run > 1:
                references.append(("repeat", output / "run-1.pickle"))
            if args.reference_dir:
                references.append(("baseline", args.reference_dir.resolve() / output.name / "run-1.pickle"))
            metrics["comparisons"] = {}
            for name, reference in references:
                # Only load our own local benchmark artifacts, never arbitrary downloaded pickle.
                with reference.open("rb") as stream:
                    expected = _canonical_payload(pickle.load(stream))
                metrics["comparisons"][name] = compare_results(
                    _comparison_payload(expected), _comparison_payload(payload),
                )
            metrics['comparison_normalization'] = (
                'safe JSON integers and floats; contiguous zero-unit panel extras sorted by property ID; '
                'all fields retained; large integer identifiers unchanged'
            )
            metrics["status"] = "passed" if all(row["equal"] for row in metrics["comparisons"].values()) else "mismatch"
            metrics["python_peak_working_set_bytes_after_snapshot_comparison"] = _peak_working_set_bytes()
    except Exception as error:
        metrics.update(status="error", error_type=type(error).__name__)
        metrics["error_message_sha256"] = hashlib.sha256(str(error).encode("utf8")).hexdigest()
        metrics["error_frames"] = [
            {"file": Path(frame.filename).name, "line": frame.lineno, "function": frame.name}
            for frame in traceback.extract_tb(error.__traceback__)
        ]
    finally:
        close = getattr(client, "close", None)
        if close is not None:
            close()
    (output / f"run-{args.run}.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf8")
    print(f"case={args.case} run={args.run} status={metrics['status']} seconds={metrics.get('seconds')}", flush=True)
    return int(metrics["status"] != "passed")


def main() -> int:
    args = _parser().parse_args()
    for name in ("source_root", "user_database", "static_database", "output_dir", "executable", "reference_dir"):
        value = getattr(args, name)
        if value is not None:
            setattr(args, name, value.resolve())
    if args.worker:
        return _worker(args)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    summary_file = args.output_dir / "summary.json"
    previous = json.loads(summary_file.read_text(encoding="utf8")) if summary_file.exists() else {}
    if previous and previous.get("backend") != args.backend:
        raise ValueError("benchmark output directory belongs to a different backend")
    results = list(previous.get("runs", ()))
    failures = 0
    summary = {"finished": False, "backend": args.backend, "runs": results}
    for specification in args.cases.split(","):
        workflow, record, mode, repeats = specification.split(":")
        if workflow not in {"history", "marginal"} or mode not in {"original", "effective"} or int(repeats) < 1:
            raise ValueError("invalid benchmark case specification")
        case = f"{workflow}:{int(record)}:{mode}"
        for run in range(1, int(repeats) + 1):
            report_file = args.output_dir / case.replace(":", "-") / f"run-{run}.json"
            if report_file.exists():
                raise ValueError("benchmark run already exists; choose a fresh output directory")
            command = [
                sys.executable, str(Path(__file__).resolve()), "--worker",
                "--source-root", str(args.source_root), "--user-database", str(args.user_database),
                "--static-database", str(args.static_database), "--output-dir", str(args.output_dir),
                "--backend", args.backend, "--case", case, "--run", str(run),
            ]
            for name, option in (("executable", "--executable"), ("reference_dir", "--reference-dir")):
                if getattr(args, name):
                    command.extend([option, str(getattr(args, name))])
            environment = dict(os.environ, PYTHONHASHSEED="0", PYTHONIOENCODING="utf-8")
            completed = subprocess.run(command, env=environment, cwd=args.source_root, check=False)
            failures += completed.returncode != 0
            if report_file.exists():
                results.append(json.loads(report_file.read_text(encoding="utf8")))
            summary = {
                "finished": False, "backend": args.backend, "runs": results,
                "failures": failures, "case_medians_seconds": {
                    key: statistics.median(row["seconds"] for row in results if row["case"] == key and "seconds" in row)
                    for key in sorted({row["case"] for row in results if "seconds" in row})
                },
            }
            (args.output_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf8")
            if completed.returncode != 0:
                return 1
    summary["finished"] = True
    (args.output_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf8")
    return int(failures != 0)


if __name__ == "__main__":
    raise SystemExit(main())
