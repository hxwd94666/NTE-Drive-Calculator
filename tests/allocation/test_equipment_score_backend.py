# 验证公共评分适配的真实 Rust 批处理、缓存隔离与目录变更，不再以 Python 公式作运行回退。
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

from src.domain.stat_catalog import StatCatalog
from src.integrations.nte_analysis_core import NativeAnalysisCancelled, NteAnalysisCoreClient
from src.models.equipment import Drive, Tape
from src.optimizer.scoring import ScoringEngine

import pytest

NTE_TEST_TIER = "core"
ROOT = Path(__file__).resolve().parents[2]


def engine():
    executable = Path(os.environ.get("NTE_ALLOCATION_POSTPROCESS_TEST_EXE") or
                      ROOT / "third_party/analysis-core/bin/nte-analysis-core.exe")
    client = NteAnalysisCoreClient.from_executable(executable, "score-fixture")
    catalog = StatCatalog.from_data({"gold_base_values": {"攻击力%": 1, "暴击率%": 1},
        "tape_main_stat_values": {"攻击力%": 37.5}, "tape_main_stats_pool": ["攻击力%"]})
    scoring = ScoringEngine(stat_catalog=catalog, roles_db={
        "A": {"weights": {"攻击力%": 1.0, "暴击率%": 2.0}},
        "B": {"weights": {"攻击力%": 1.0, "暴击率%": 2.0}},
    }, scoring_client=client)
    drive = Drive(uid="drive", shape_id="H_4", area=4, quality="Gold",
                  sub_stats={"攻击力%": 6, "暴击率%": 2}, main_stats={"攻击力": 84, "生命值": 1120})
    tape = Tape(uid="tape", area=15, quality="Gold", set_name="fixture", main_stats="攻击力%", main_value=18.75,
                sub_stats={"暴击率%": 2})
    return scoring, client, drive, tape


def test_native_public_score_batch_is_one_call_and_keeps_mask_main_reference_and_grade_semantics():
    scoring, client, drive, tape = engine()
    profiles = [{"key": "A", "weights": scoring.roles_db["A"]["weights"]},
                {"key": "B", "weights": scoring.roles_db["B"]["weights"], "zero_weight_stats": ["暴击率%"]}]
    with patch.object(client, "score_equipment", wraps=client.score_equipment) as dispatch:
        scoring.prepare_scores([drive, tape], profiles=profiles, assign_scores=True)
        assert drive.role_scores == {"A": 40.0, "B": 40.0}
        # B的暴击副词条置零，实际半级主词条仍为25分，不再保留旧卡带副词条加分。
        assert tape.role_scores == {"A": 91.67, "B": 25.0}
        for role, zeros in (("A", ()), ("B", ("暴击率%",))):
            weights = scoring.roles_db[role]["weights"]
            maximum = scoring.max_theoretical_weight(weights, zero_weight_stats=zeros)
            assert scoring.calculate_drive_score(drive, weights, maximum, zero_weight_stats=zeros) == 40.0
            assert scoring.calculate_cartridge_score(tape, weights, maximum, zero_weight_stats=zeros) == tape.role_scores[role]
            assert scoring.flexible_weight("暴击率%", weights) == 2.0
        assert scoring.get_grade_tag(91.67, 15) == "SS"
        assert scoring.get_grade_tag(25.0, 15) == "D"
        dispatch.assert_called_once()


def test_scoring_catalog_change_and_distinct_main_weights_do_not_reuse_stale_cache():
    scoring, client, drive, tape = engine()
    weights = scoring.roles_db["A"]["weights"]
    profile = {"key": "A", "weights": weights, "main_weights": {"攻击力%": 0.4}}
    scoring.prepare_scores([drive, tape], profiles=[profile])
    assert scoring.calculate_cartridge_score(tape, weights, 3.0, profile["main_weights"]) == 76.67
    scoring.stat_catalog.tape_main_values["攻击力%"] = 75.0
    with patch.object(client, "score_equipment", wraps=client.score_equipment) as dispatch:
        assert scoring.calculate_cartridge_score(tape, weights, 3.0, profile["main_weights"]) == 71.67
        dispatch.assert_called_once()


def test_batch_cancellation_stops_before_dispatch_and_does_not_replace_cached_values():
    scoring, client, drive, tape = engine()
    def cancelled():
        raise NativeAnalysisCancelled("cancelled")
    with patch.object(client, "score_equipment", wraps=client.score_equipment) as dispatch:
        with pytest.raises(NativeAnalysisCancelled):
            scoring.prepare_scores([drive, tape], checkpoint=cancelled)
        dispatch.assert_not_called()


def test_grade_batch_preserves_duplicates_order_and_returns_values_beyond_cache_capacity():
    scoring, client, _, _ = engine()
    pairs = [(float(index), 15) for index in range(8200)]
    with patch.object(client, 'score_equipment', wraps=client.score_equipment) as dispatch:
        grades = scoring.get_grade_tags([*pairs, pairs[0], pairs[9]])
        assert dispatch.call_count == 3  # 协议每批最多 4096，不逐条启动组件。
        assert grades[0] == grades[-2] == 'D'
        assert grades[9] == grades[-1]
        assert grades[-3] == 'ACE'
    for index in (0, 900, 4095, 8199):
        assert grades[index] == scoring.get_grade_tag(*pairs[index])
    with patch.object(client, 'score_equipment', wraps=client.score_equipment) as dispatch:
        def cancel():
            raise NativeAnalysisCancelled('cancelled')
        with pytest.raises(NativeAnalysisCancelled):
            scoring.get_grade_tags([(1.234, 2)], checkpoint=cancel)
        dispatch.assert_not_called()


def test_bulk_only_consumer_keeps_scores_and_returns_frozen_weight_matrix():
    scoring, client, drive, tape = engine()
    scoring.prepare_scores([drive,tape],assign_scores=True,cache_items=False)
    assert drive.role_scores=={'A':40.0,'B':40.0}
    matrix=scoring.prepare_weight_queries([scoring.roles_db['A']['weights']],['攻击力%','暴击率%'])
    assert matrix==[[1.0,2.0]]
    assert scoring.calculate_drive_score(drive,scoring.roles_db['A']['weights'],3.0)==40.0
