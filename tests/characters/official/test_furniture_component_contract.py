# 验证三项家具所需组件能力及旧二项执行物拒绝路径，不启动外部进程。
import hashlib
import json

import pytest

from src.integrations.analysis_core import analysis_core_release as release
from src.integrations.analysis_core.nte_analysis_core import ENGINE_VERSION, NativeAnalysisError


NTE_TEST_TIER = 'core'


def test_old_component_never_silently_drops_defense(tmp_path, monkeypatch):
    bundle = tmp_path / 'third_party/analysis-core'
    binary = bundle / 'bin/nte-analysis-core.exe'
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b'isolated component identity fixture; not executable')
    manifest = dict(engine='nte-analysis-core', engine_version=ENGINE_VERSION,
                    sha256=hashlib.sha256(binary.read_bytes()).hexdigest(), capabilities=['battle_page_v1'])
    metadata = bundle / 'component.json'
    metadata.write_text(json.dumps(manifest), encoding='utf8')
    data = tmp_path / 'data'
    data.mkdir()
    (data / 'manifest.json').write_text(json.dumps({'database': {'dataset_id': 'fixture'}}), encoding='utf8')
    monkeypatch.setattr(release, 'bundled_root', lambda: tmp_path)
    with pytest.raises(NativeAnalysisError):
        release.create_bundled_analysis_client(static_database_path=data / 'game_static.sqlite3')
    manifest['capabilities'].append('furniture_attributes_v1')
    metadata.write_text(json.dumps(manifest), encoding='utf8')
    client = release.create_bundled_analysis_client(static_database_path=data / 'game_static.sqlite3')
    assert client is not None and client.executable == binary.resolve()
