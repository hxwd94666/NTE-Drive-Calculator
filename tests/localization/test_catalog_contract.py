# 统一核对发行和示例语言目录、参数、旧补丁回退与大小边界，不启动界面。
import json
from pathlib import Path

import pytest

from src.domain.localization.catalog import CatalogError, MAX_CATALOG_MESSAGES, make_catalog
from src.integrations.preferences.locale_files import bundled_text_catalog, MAX_CATALOG_BYTES


NTE_TEST_TIER = "core"


def test_catalog_example_rendering_parameters_limits_and_legacy_fallback():
    root = Path(__file__).resolve().parents[2]
    base = bundled_text_catalog()
    example_path = root / 'docs/examples/locales/qps-ploc.json'
    payload = json.loads(example_path.read_text(encoding='utf8'))
    sample = make_catalog(payload, base=base)
    # 示例和旧用户补丁都允许缺项；make_catalog 校验显式参数，其余继承发行中文。
    old = make_catalog(dict(format_version=1, catalog_id='nte-ui', locale='en-US',
                            display_name='Old', messages={}), base=base)
    assert old.messages == base.messages
    for key in base.messages:
        assert sample.parameters[key] == base.parameters[key]
        values = {name: '<b>plain input</b>' for name in base.parameters[key]}
        assert isinstance(sample.render(key, **values), str)
    assert (root / 'assets/locales/zh-CN.json').stat().st_size < MAX_CATALOG_BYTES
    assert example_path.stat().st_size < MAX_CATALOG_BYTES
    with pytest.raises(CatalogError, match='invalid_messages'):
        make_catalog(dict(format_version=1, catalog_id='nte-ui', locale='zh-CN', display_name='Oversize',
                          messages={f'fixture.key{i}': 'x' for i in range(MAX_CATALOG_MESSAGES + 1)}))
