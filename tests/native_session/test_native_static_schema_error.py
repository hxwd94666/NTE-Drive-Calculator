# 核对正式组件明确拒绝新静态结构时给出匹配组件提示，未知或身份不符的错误仍收敛为通用故障。
import json
from types import SimpleNamespace

import pytest

from src.integrations.analysis_core.nte_analysis_core import (
    ENGINE_VERSION, RESPONSE_SCHEMA, NativeAnalysisError, NteAnalysisCoreClient,
)


NTE_TEST_TIER = "core"


def response():
    return {"schema_version": RESPONSE_SCHEMA, "engine_version": ENGINE_VERSION,
            "error": {"code": "battle_static_schema_unsupported", "message": "PRIVATE_FIXTURE_PAYLOAD"}}


def reject(payload, status=2):
    client = SimpleNamespace(engine_version=ENGINE_VERSION)
    return NteAnalysisCoreClient._check_exit(client, status, json.dumps(payload).encode("utf-8"))


def test_known_schema_rejection_is_actionable_without_exposing_component_payload():
    with pytest.raises(NativeAnalysisError) as error:
        reject(response())
    assert "静态资料结构" in str(error.value) and "更新" in str(error.value)
    assert "原始战报保持不变" in str(error.value)
    assert "PRIVATE_FIXTURE_PAYLOAD" not in str(error.value)


@pytest.mark.parametrize("change", ["engine", "schema", "unknown_code", "missing_error", "bad_error", "missing_code"])
def test_unknown_or_mismatched_errors_do_not_claim_a_schema_cause(change):
    payload = response()
    if change == "engine":
        payload["engine_version"] = "unknown-engine"
    elif change == "schema":
        payload["schema_version"] = "unknown-protocol"
    elif change == "unknown_code":
        payload["error"]["code"] = "unrecognized_error"
    elif change == "missing_error":
        payload.pop("error")
    elif change == "bad_error":
        payload["error"] = []
    else:
        payload["error"].pop("code")
    with pytest.raises(NativeAnalysisError, match="退出码 2") as error:
        reject(payload)
    assert "静态资料结构" not in str(error.value)
    assert "PRIVATE_FIXTURE_PAYLOAD" not in str(error.value)


def test_other_exit_status_does_not_trust_structured_error_and_success_is_not_overridden():
    with pytest.raises(NativeAnalysisError, match="退出码 1"):
        reject(response(), status=1)
    assert reject(response(), status=0) is None
