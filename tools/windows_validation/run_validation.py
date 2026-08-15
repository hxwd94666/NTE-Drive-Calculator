# 独立执行 Windows 预检、引导式人工确认和本地证据报告生成。
"""Run maintenance-only Windows validation profiles."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.windows_validation.log_probe import inspect_logs, timestamp_logs
from src.integrations.vision.mouse_scan_runtime import probe_mouse_scan_runtime
from tools.windows_validation.models import CheckResult, StepResult, ValidationReport
from tools.windows_validation.mouse_scan_probe import (
    compare_mouse_scan_to_account,
    inspect_mouse_scan_report,
)
from tools.windows_validation.preflight import (
    default_artifact_paths,
    environment_evidence,
    file_evidence,
)
from tools.windows_validation.process_probe import ManagedApplication
from tools.windows_validation.profiles import PROFILE_BY_KEY, PROFILES
from tools.windows_validation.report import write_report
from tools.windows_validation.sqlite_probe import sqlite_summary


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="NTE Windows 半自动验证器（维护者工具）")
    parser.add_argument("--target", type=Path, help="便携版、安装版或源码启动目标")
    parser.add_argument("--account-db", type=Path)
    parser.add_argument("--static-db", type=Path, default=ROOT / "data" / "game_static.sqlite3")
    parser.add_argument("--log-dir", type=Path)
    parser.add_argument("--mouse-scan-report", type=Path)
    parser.add_argument(
        "--artifact",
        action="append",
        type=Path,
        default=[],
        help="额外记录版本、大小和 SHA-256 的关键文件",
    )
    parser.add_argument(
        "--profile",
        action="append",
        choices=(*PROFILE_BY_KEY, "all"),
        default=[],
    )
    parser.add_argument("--non-interactive", action="store_true")
    parser.add_argument("--launch-executable", type=Path)
    parser.add_argument("--launch-arg", action="append", default=[])
    parser.add_argument("--keep-running", action="store_true")
    parser.add_argument("--output-root", type=Path, default=ROOT / "build" / "windows-validation")
    return parser.parse_args(argv)


def selected_profiles(keys: list[str]) -> tuple:
    if not keys or "all" in keys:
        return PROFILES
    return tuple(PROFILE_BY_KEY[key] for key in dict.fromkeys(keys))


def _prompt_result(title: str, instruction: str) -> tuple[str, str]:
    print(f"\n[{title}]\n{instruction}")
    while True:
        answer = input("结果 [p=通过 / f=失败 / s=跳过 / q=结束]：").strip().casefold()
        if answer in {"p", "f", "s", "q"}:
            break
    if answer == "q":
        raise KeyboardInterrupt
    note = input("备注（可留空）：").strip()
    return {"p": "passed", "f": "failed", "s": "skipped"}[answer], note


def _profile_checks(profile, args, timestamp_before: set[str]) -> tuple[CheckResult, ...]:
    logs = inspect_logs(
        args.log_dir,
        expected_events=profile.expected_events,
        roots=tuple(path for path in (args.account_db, args.log_dir) if path is not None),
    )
    checks = [
        CheckResult(
            "logs",
            "passed" if all(logs.get("expected_events", {}).values()) else "warning",
            "已检查预期日志事件",
            logs,
        )
    ]
    if profile.key == "startup":
        after = {path.name for path in timestamp_logs(args.log_dir)}
        new_files = sorted(after - timestamp_before)
        checks.append(
            CheckResult(
                "timestamp-session-log",
                "passed" if new_files else "warning",
                "检查本次是否生成新的独立时间戳日志",
                {"new_files": new_files},
            )
        )
    mouse_scan = None
    if profile.key == "vision":
        runtime = probe_mouse_scan_runtime()
        checks.append(
            CheckResult(
                "mouse-visual-runtime",
                "passed" if runtime.ok else "warning",
                "不发送输入地检查截图、鼠标和 Win32 运行依赖",
                {
                    "module_versions": dict(runtime.module_versions),
                    "failures": list(runtime.failures),
                },
            )
        )
        mouse_scan = inspect_mouse_scan_report(args.mouse_scan_report)
        checks.append(
            CheckResult(
                "mouse-visual-scan-report",
                "passed" if mouse_scan.get("passed") else "warning",
                "只读检查鼠标扫描分辨率、完整数量、预检、逐页序号和滚轮量",
                mouse_scan,
            )
        )
    if profile.key in {
        "account-switch",
        "nte-core-sync",
        "vision",
        "calculation",
        "warehouse",
        "assembly",
    }:
        account_summary = sqlite_summary(args.account_db)
        checks.append(
            CheckResult(
                "account-database",
                "passed" if args.account_db and args.account_db.is_file() else "warning",
                "以只读方式采集账号数据库摘要",
                account_summary,
            )
        )
        if profile.key == "vision":
            snapshot_match = compare_mouse_scan_to_account(mouse_scan or {}, account_summary)
            checks.append(
                CheckResult(
                    "mouse-visual-current-snapshot",
                    "passed" if snapshot_match.get("passed") else "warning",
                    "交叉检查报告与账号 SQLite 当前视觉快照",
                    snapshot_match,
                )
            )
    return tuple(checks)


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = parse_args(argv)
    now = datetime.now()
    session_id = now.strftime("%Y%m%d_%H%M%S")
    output_dir = args.output_root / session_id
    target = args.target or args.launch_executable
    tracked_files = tuple(dict.fromkeys(
        path.resolve()
        for path in (
            args.static_db,
            target,
            *default_artifact_paths(target),
            *args.artifact,
        )
        if path is not None and path.is_file()
    ))
    report = ValidationReport(
        session_id=session_id,
        started_at=now.isoformat(timespec="seconds"),
        target=str(target or ""),
        environment=environment_evidence(target),
        hashes_before=file_evidence(tracked_files),
    )
    timestamp_before = {path.name for path in timestamp_logs(args.log_dir)}
    managed = (
        ManagedApplication(args.launch_executable, tuple(args.launch_arg))
        if args.launch_executable is not None
        else None
    )
    exit_code = 0
    try:
        if managed is not None:
            report.environment["launched_pid"] = managed.start()
        for profile in selected_profiles(args.profile):
            if args.non_interactive:
                status, note = "skipped", "非交互模式只执行自动预检与证据采集。"
            else:
                try:
                    status, note = _prompt_result(profile.title, profile.instruction)
                except KeyboardInterrupt:
                    report.steps.append(
                        StepResult(profile.key, profile.title, "skipped", "维护者提前结束。")
                    )
                    break
            checks = _profile_checks(profile, args, timestamp_before)
            report.steps.append(StepResult(profile.key, profile.title, status, note, checks))
            if status == "failed":
                exit_code = 1
    finally:
        if managed is not None and not args.keep_running:
            report.environment["launched_exit_code"] = managed.stop()
        report.hashes_after = file_evidence(tracked_files)
        report.finished_at = datetime.now().isoformat(timespec="seconds")
        markdown_path, json_path = write_report(
            report,
            output_dir,
            private_roots=tuple(
                path.parent if path.suffix else path
                for path in (args.account_db, args.log_dir)
                if path is not None
            ),
        )
        print(f"验证报告：{markdown_path}")
        print(f"机器数据：{json_path}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
