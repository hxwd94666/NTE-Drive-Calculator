# 验证汇总器只读取专用数值事件，不回显账号、装备、异常或普通日志。
from tools.quality.summarize_request_timings import summarize


def test_summary_aggregates_and_does_not_copy_other_log_fields():
    key = "a" * 32
    lines = [
        f'warehouse.timing_stage | operation_id="{key}" stage="solver" sample_count=2 duration_ms=10 max_duration_ms=6 account_name="private"',
        f'warehouse.timing_stage | operation_id="{key}" stage="solver" sample_count=1 duration_ms=8 max_duration_ms=8',
        f'warehouse.timing_boundary | operation_id="{key}" boundary="worker_received" elapsed_ms=22',
        'warehouse.state_apply_failed | error="private"',
    ]
    rows = summarize(lines)
    assert rows[0]["stages"]["solver"] == {"sample_count": 3, "duration_ms": 18, "max_duration_ms": 8}
    assert rows[0]["boundaries"][0]["elapsed_ms"] == 22
    assert "private" not in repr(rows)
    assert summarize(lines, request_id="b" * 32) == []
    assert summarize(["old log without phase metrics"]) == []


def test_normal_summary_and_battle_details_remain_usable_without_fabricated_stages():
    key = 'b' * 32
    lines = [f'battle_report.timing_summary | operation_id="{key}" result="returned" elapsed_ms=42.5 '
             'stage_count=3 slow=false boundary="worker_finished" account_name="private"']
    rows = summarize(lines)
    assert rows[0]['summary'] == dict(result='returned', elapsed_ms=42.5, stage_count=3,
                                     slow=False, boundary='worker_finished')
    assert rows[0]['stages'] == {} and 'private' not in repr(rows)
    lines.append(f'battle_report.timing_stage | operation_id="{key}" stage="battle_page.project" duration_ms=4 sample_count=1')
    assert summarize(lines)[0]['stages']['battle_page.project']['duration_ms'] == 4
