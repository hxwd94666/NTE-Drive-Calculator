# 验证一次评分矩阵的冻结顺序、协议维度和取消门禁，不用 Python 实现新的评分公式。
from __future__ import annotations

from copy import deepcopy
import json
from unittest.mock import Mock

import pytest

from src.domain.stat_catalog import StatCatalog
from src.integrations.equipment_scoring import freeze_equipment_scoring, score_equipment
from src.integrations.nte_analysis_core import NativeAnalysisCancelled, NativeAnalysisError

NTE_TEST_TIER = "core"


def request():
    catalog = StatCatalog.from_data({"gold_base_values": {"攻击力%": 1.25}, "tape_main_stat_values": {"攻击力%": 37.5},
                                    "tape_main_stats_pool": ["攻击力%"]})
    items = [{"item_type": "tape", "area": 15, "quality": "Purple", "main_stats": "攻击力%",
              "main_value": 18.75, "sub_stats": {"攻击力%": 5.0}}]
    return freeze_equipment_scoring(catalog, items, [{"key": "profile", "weights": {"攻击力%": 1.0}}],
                                   queries=["攻击力%"], grade_queries=[{"score": 0, "area": 0}])


def response(inner):
    return {"schema_version": "nte-analysis-response-v1", "engine_version": "0.3.0", "dataset_version": "score-fixture",
            "batch_kind": "equipment_scoring_v1", "version": 1, "model_id": "equipment_base_score_v1",
            "input_identity": deepcopy(inner["input_identity"]), "item_keys": ["item-0"], "queries": ["攻击力%"],
            "profiles": [{"key": "profile", "max_weight": 1.0, "scores": [105.0], "grades": ["SSS"], "weights": [1.0]}],
            "grade_results": ["D"], "elapsed_millis": 1}


def client(value):
    return Mock(engine_version="0.3.0", dataset_version="score-fixture", supports_equipment_scoring=True,
                _run=Mock(return_value=json.dumps(value).encode("utf8")))


def test_equipment_scoring_batch_keeps_frozen_catalog_reference_and_uses_one_dispatch():
    inner = request()
    frozen = deepcopy(inner)
    assert inner["items"][0]["main_reference_value"] == 37.5
    assert inner["items"][0]["main_value"] == 18.75
    transport = client(response(inner))
    result = score_equipment(transport, inner)
    transport._run.assert_called_once()
    sent = json.loads(transport._run.call_args.args[0])
    assert sent["request"] == frozen and inner == frozen
    assert result["profiles"][0]["scores"] == [105.0]
    assert sent["dataset_version"] == "score-fixture"


def test_equipment_scoring_rejects_partial_matrix_identity_and_non_numeric_output():
    inner = request()
    for field, value in (("input_identity", {}), ("item_keys", ["other"]), ("version", True)):
        bad = response(inner)
        bad[field] = value
        with pytest.raises(NativeAnalysisError):
            score_equipment(client(bad), inner)
    for field, value in (("scores", []), ("scores", [True]), ("grades", ["unknown"]), ("weights", [])):
        bad = response(inner)
        bad["profiles"][0][field] = value
        with pytest.raises(NativeAnalysisError):
            score_equipment(client(bad), inner)


def test_equipment_scoring_cancellation_and_missing_capability_do_not_dispatch():
    inner = request()
    transport = client(response(inner))
    transport._checkpoint.side_effect = NativeAnalysisCancelled("cancelled")
    with pytest.raises(NativeAnalysisCancelled):
        score_equipment(transport, inner)
    transport._run.assert_not_called()
    transport = client(response(inner))
    transport.supports_equipment_scoring = False
    with pytest.raises(NativeAnalysisError):
        score_equipment(transport, inner)
    transport._run.assert_not_called()


def test_equipment_scoring_identity_preserves_alias_precedence_and_is_detached():
    catalog = StatCatalog.from_data({"gold_base_values": {"伤害增加%": 1.0}})
    weights = {"通用伤害增强%": 1.0, "通用伤害增强": 2.0}
    first = freeze_equipment_scoring(catalog, [], [{"key": "p", "weights": weights}], queries=["伤害增加%"])
    second = freeze_equipment_scoring(catalog, [], [{"key": "p", "weights": dict(reversed(list(weights.items())))}],
                                      queries=["伤害增加%"])
    assert first["input_identity"]["input_sha256"] != second["input_identity"]["input_sha256"]
    weights.clear()
    catalog.gold_base_values.clear()
    assert list(first["profiles"][0]["weights"].values()) == [1.0, 2.0]
    assert first["stats"]["gold_base_values"] == {"伤害增加%": 1.0}

