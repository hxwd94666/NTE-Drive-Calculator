# 编辑事实严格响应不协商逐击分片，避免额外根字段阻断编辑与反事实候选。
import json
from unittest.mock import patch

from src.integrations.analysis_core.nte_analysis_core import NteAnalysisCoreClient, RESPONSE_SCHEMA


def test_editor_request_excludes_fragments_but_hit_request_keeps_them(tmp_path):
    executable = tmp_path / "nte-analysis-core.exe"
    executable.touch()
    client = NteAnalysisCoreClient(executable, "fixture")
    client.supports_battle_page = True
    client.supports_native_payload_fragments = True
    request = dict(account_id="test", generation=0, battle_record_id=1, detail_level="editor")
    observed = []
    def run(raw, **kwargs):
        sent = json.loads(raw)
        observed.append(sent["request"])
        return json.dumps(dict(schema_version=RESPONSE_SCHEMA, engine_version=client.engine_version,
            dataset_version="fixture", batch_kind="battle_page_v1", account_id="test", generation=0,
            battle_record_id=1, compute_elapsed_ns=1, result={"editor_facts": {}})).encode()
    with patch.object(client, "_run", side_effect=run):
        client.load_battle_page(request)
        client.load_battle_page({**request, "detail_level": "hit"})
    assert "native_payload_encoding" not in observed[0]
    assert observed[1]["native_payload_encoding"] == "fragments_v1"
