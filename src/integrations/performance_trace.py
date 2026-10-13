# 校验细分性能回包并保存账号内的有界诊断摘要。
import json
from datetime import datetime, timezone
from uuid import uuid4

SERVICES = ('hud_frame', 'hud_event', 'hud_value_event', 'snapshot_pulse',
            'snapshot_read', 'combat_clock', 'combat_observer_pulse')
PHASES = ('original', 'gate', 'inbox', 'discovery', 'setup', 'boss', 'team',
          'nameplate', 'infoLog', 'draw', 'body', 'outside', 'gap', 'teamCooldowns',
          'teamTextures', 'teamMirror', 'teamCanvas')


def validate_trace(value, trace_id):
    if (not isinstance(value, dict) or value.get('trace_id') != trace_id
            or any(type(value.get(k)) is not bool for k in ('active', 'stopping', 'failed', 'complete'))
            or any(type(value.get(k)) is not int or not 0 <= value[k] < 2**63 for k in ('written', 'dropped'))
            or not isinstance(value.get('path'), str) or len(value['path']) > 32768
            or not isinstance(value.get('stop_reason'), str)):
        raise ValueError('细分性能回包身份或状态无效')
    summaries = value.get('summaries')
    if not isinstance(summaries, list) or len(summaries) > len(SERVICES):
        raise ValueError('细分性能摘要格式无效')
    seen = set()
    for summary in summaries:
        if not isinstance(summary, dict) or summary.get('source') not in SERVICES or summary['source'] in seen:
            raise ValueError('细分性能来源无效')
        seen.add(summary['source'])
        phases = summary.get('phases_us')
        if not isinstance(phases, dict) or set(phases) != set(PHASES):
            raise ValueError('细分性能阶段不匹配')
        for row in phases.values():
            if not isinstance(row, dict) or type(row.get('n')) is not int or not 0 <= row['n'] <= 600:
                raise ValueError('细分性能样本数无效')
            for key in ('p95', 'p99', 'max'):
                metric = row.get(key)
                if (row['n'] == 0 and metric is not None) or (row['n'] > 0 and
                        (type(metric) is not int or not 0 <= metric < 2**63)):
                    raise ValueError('细分性能缺失值或耗时无效')
    return value


class TraceJournal:
    def __init__(self, directory, trace_id, services):
        directory = directory / 'performance'
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / f'detail_{datetime.now():%Y%m%d_%H%M%S}_{uuid4().hex[:8]}.jsonl'
        self.stream = self.path.open('x', encoding='utf-8')
        self.bytes = 0
        self.previous = None
        self.write({'kind': 'session_start', 'schema': 'calc.performance.trace/1',
                    'trace_id': trace_id, 'services': services, 'source': 'nte.performance/2'})

    def write(self, row):
        text = json.dumps({**row, 'utc': datetime.now(timezone.utc).isoformat()}, ensure_ascii=False) + '\n'
        size = len(text.encode('utf-8'))
        if self.bytes + size > 64 * 1024 * 1024:
            raise OSError('细分性能日志达到容量上限')
        self.stream.write(text)
        self.stream.flush()
        self.bytes += size

    def sample(self, value):
        # 仅持久化发生变化的摘要窗口及已确认终态，不把重复轮询累积成日志。
        identity = (value['summaries'], value['active'], value['failed'], value['complete'])
        if identity != self.previous:
            self.write({'kind': 'status', **value})
            self.previous = identity

    def close(self):
        self.stream.close()
