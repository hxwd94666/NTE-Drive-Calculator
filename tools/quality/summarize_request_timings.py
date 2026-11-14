# 将现有请求计时日志汇总为阶段表，只输出关联标识和数值，不回显业务日志。
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
from collections import OrderedDict


EVENT = re.compile(r"\b(allocation|warehouse|battle_report)\.timing_(stage|boundary|summary)\b")
FIELD = re.compile(r'\b([a-z][a-z0-9_]*)=("(?:[^"\\]|\\.)*"|true|false|\d+(?:\.\d+)?)')
NUMERIC = {"duration_ms", "max_duration_ms", "sample_count", "error_count", "cancelled_count"}


def summarize(lines, *, request_id=None):
    """只接受专用事件及数值字段；旧日志无阶段记录时返回空，而不是推测为零。"""
    requests = OrderedDict()
    for line in lines:
        event = EVENT.search(line)
        if event is None:
            continue
        fields = {m.group(1): json.loads(m.group(2)) for m in FIELD.finditer(line)}
        identity = fields.get("operation_id")
        if not isinstance(identity, str) or not re.fullmatch(r"[a-f0-9]{32}", identity):
            continue
        if request_id is not None and identity != request_id:
            continue
        key = event.group(1), identity
        request = requests.setdefault(key, {"feature": key[0], "operation_id": identity,
                                            "stages": {}, "boundaries": []})
        if event.group(2) == 'summary':
            summary = {name: fields[name] for name in ('elapsed_ms', 'stage_count', 'slow')
                       if type(fields.get(name)) in (int, float, bool)}
            if fields.get('result') in {'returned', 'failed', 'cancelled'}:
                summary['result'] = fields['result']
            boundary = fields.get('boundary')
            if isinstance(boundary, str) and re.fullmatch(r'[a-z0-9_.]{1,80}', boundary):
                summary['boundary'] = boundary
            request['summary'] = summary
            continue
        if event.group(2) == "boundary":
            boundary = fields.get("boundary")
            if isinstance(boundary, str) and re.fullmatch(r"[a-z0-9_.]{1,80}", boundary):
                request["boundaries"].append({"boundary": boundary, "elapsed_ms": fields.get("elapsed_ms")})
            continue
        stage = fields.get("stage")
        if not isinstance(stage, str) or not re.fullmatch(r"[a-z0-9_.]{1,80}", stage):
            continue
        row = request["stages"].setdefault(stage, {})
        for name, value in fields.items():
            if name not in NUMERIC and not name.endswith(("_count", "_bytes", "_enabled", "_hit", "_ms")):
                continue
            if type(value) not in (int, float, bool):
                continue
            if name.startswith("max_") or name == "peak_lag_ms":
                row[name] = max(row.get(name, 0), value)
            elif type(value) is bool:
                row[name] = value
            else:
                row[name] = row.get(name, 0) + value
    return list(requests.values())


def main():
    parser = argparse.ArgumentParser(description="汇总已有计算／仓库／战报计时日志；不运行计算或连接游戏。")
    parser.add_argument("logs", nargs="+", type=Path)
    parser.add_argument("--request-id")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    def lines():
        for path in args.logs:
            with path.open(encoding="utf-8-sig", errors="replace") as source:
                yield from source

    result = summarize(lines(), request_id=args.request_id)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    if not result:
        print("未发现请求计时记录；旧版日志不补零，也不推测阶段耗时。")
        return
    print("父子阶段可能互相包含，请勿将各行耗时直接相加。")
    for request in result:
        print(f"\n{request['feature']} {request['operation_id']}")
        if 'summary' in request:
            print('摘要：' + json.dumps(request['summary'], ensure_ascii=False))
        if not request['stages']:
            print('未记录阶段明细；需要时启用详细日志，不从摘要补造阶段。')
            continue
        print("阶段 | 次数 | 总耗时 ms | 单次最大 ms")
        for stage, row in request["stages"].items():
            print(f"{stage} | {row.get('sample_count', 0)} | "
                  f"{row.get('duration_ms', '—')} | {row.get('max_duration_ms', '—')}")
        print("边界：" + json.dumps(request["boundaries"], ensure_ascii=False))


if __name__ == "__main__":
    main()
