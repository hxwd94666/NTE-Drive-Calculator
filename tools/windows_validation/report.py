# 将验证结果写为本地 JSON 与 Markdown，不上传日志、截图或数据库。
"""Local report rendering."""

from __future__ import annotations

import json
from pathlib import Path

from tools.windows_validation.models import ValidationReport
from tools.windows_validation.redaction import redact_value


def write_report(
    report: ValidationReport,
    output_dir: Path,
    *,
    private_roots: tuple[Path, ...] = (),
) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    payload = redact_value(report.as_dict(), roots=private_roots)
    json_path = output_dir / "report.json"
    markdown_path = output_dir / "report.md"
    json_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    lines = [
        "# Windows 半自动验证报告",
        "",
        f"- 会话：`{payload['session_id']}`",
        f"- 开始：{payload['started_at']}",
        f"- 结束：{payload.get('finished_at') or '未结束'}",
        f"- 目标：`{payload['target'] or '未指定'}`",
        "",
        "## 环境",
        "",
        "```json",
        json.dumps(payload["environment"], ensure_ascii=False, indent=2),
        "```",
        "",
        "## 功能验证",
        "",
    ]
    for step in payload["steps"]:
        lines.extend(
            (
                f"### {step['title']}：{step['status']}",
                "",
                step.get("note") or "无备注。",
                "",
            )
        )
        for check in step.get("checks", []):
            lines.append(
                f"- `{check['status']}` {check['summary']} (`{check['key']}`)"
            )
        lines.append("")
    lines.extend(
        (
            "## 静态文件哈希",
            "",
            "```json",
            json.dumps(
                {
                    "before": payload["hashes_before"],
                    "after": payload["hashes_after"],
                },
                ensure_ascii=False,
                indent=2,
            ),
            "```",
            "",
        )
    )
    markdown_path.write_text("\n".join(lines), encoding="utf-8")
    return markdown_path, json_path

