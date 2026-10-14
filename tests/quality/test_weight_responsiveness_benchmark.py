# 验证性能样本口径及真实页面业务失败后的物理收尾，不固化耗时或装饰布局。
from pathlib import Path
import json
import shutil
import sys

import pytest

from tools.quality import benchmark_weight_responsiveness as benchmark

NTE_TEST_TIER = 'core'
ROOT = Path(__file__).resolve().parents[2]


def test_percentile_sample_count_and_empty_are_not_silent_success():
    values = list(range(1, 31))
    result = benchmark.summarize(values)
    assert result['samples'] == 30
    assert (result['p95_ms'], result['p99_ms'], result['max_ms']) == (29, 30, 30)
    with pytest.raises(ValueError):
        benchmark.summarize([])


def test_fixture_identity_covers_manifest_changes(tmp_path):
    (tmp_path / 'config').mkdir()
    for name in ('user.sqlite3', 'shared.sqlite3', 'game_static.sqlite3', 'manifest.json',
                 'config/stats.json', 'config/workshop_weight_template.json'):
        (tmp_path / name).write_text('{}', encoding='utf-8')
    (tmp_path / 'ui-benchmark-fixture.json').write_text(
        json.dumps({'isolated': True, 'synthetic': True}), encoding='utf-8')
    before = benchmark.fixture_inputs(tmp_path)
    (tmp_path / 'manifest.json').write_text('{"changed": true}', encoding='utf-8')
    after = benchmark.fixture_inputs(tmp_path)
    assert before['manifest.json'] != after['manifest.json']
    assert {key: value for key, value in before.items() if key != 'manifest.json'} == {
        key: value for key, value in after.items() if key != 'manifest.json'}
    (tmp_path / 'manifest.json').unlink()
    with pytest.raises(ValueError, match='fixture 成员'):
        benchmark.fixture_inputs(tmp_path)


def test_real_warning_failure_still_reaps_page_before_clone_cleanup(tmp_path):
    from src.storage.sqlite.user_data_dao import UserDataDao
    from tests.allocation.static_release_helpers import publish_test_manifest
    fixture = tmp_path / 'fixture'
    fixture.mkdir()
    (fixture / 'config').mkdir()
    shutil.copy2(ROOT / 'data/game_static.sqlite3', fixture / 'game_static.sqlite3')
    publish_test_manifest(fixture / 'game_static.sqlite3')
    shutil.copy2(ROOT / 'config/stats.json', fixture / 'config/stats.json')
    (fixture / 'config/workshop_weight_template.json').write_text(json.dumps({
        'schema_version': 1, 'payload_sha256': 'warning-fixture', 'characters': {}}), encoding='utf-8')
    (fixture / 'ui-benchmark-fixture.json').write_text(json.dumps({'isolated': True, 'synthetic': True}), encoding='utf-8')
    for name in ('user.sqlite3', 'shared.sqlite3'):
        with UserDataDao(fixture / name, account_id='weight-benchmark'):
            pass
    assert benchmark.fixture_inputs(fixture)['manifest.json'] == benchmark.sha256(fixture / 'manifest.json')
    output = tmp_path / 'warning.json'
    # 专用 worker 改导入器和日志 sinks；完整初始化隔离到本次受控子进程，不污染 pytest。
    code = """
import sys
from pathlib import Path
from types import SimpleNamespace
from tools.quality import benchmark_weight_responsiveness as benchmark
root, fixture, output = map(Path, sys.argv[1:])
benchmark.isolate_source(root)
from src.services.characters.basic_weight.service import BasicWeightService
def failed_save(*_args, **_kwargs):
    raise RuntimeError('controlled commit failure')
BasicWeightService.save_changes = failed_save
args = SimpleNamespace(source_root=root, fixture=fixture, output=output,
                       iterations=1, worker_mode='cold', one_cpu=False, qt_platform='offscreen')
raise SystemExit(benchmark.worker(args))
"""
    execution = benchmark.run_child([sys.executable, '-c', code, str(ROOT), str(fixture), str(output)],
                                    ROOT, tmp_path / 'failure-worker.json', timeout=90)
    assert execution['exit_status'] == 1, execution
    assert execution['cleanup'] is None, execution  # 正常业务失败自行完成，非父进程强制终止。
    result = json.loads(output.read_text(encoding='utf-8'))
    assert result['status'] == 'failed'
    assert 'controlled commit failure' in result['error']
    assert result['failure_cleanup'] == 'physical tasks settled and page destroyed'
    assert result['inputs_hashes_unchanged']
    assert not result['imports']['foreign_project_modules']
    assert not list(fixture.glob('weight-measure-*'))
