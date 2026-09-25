# 验证发行甲硬币修复只接纳来源明确的付费产出并保留玛门方斯。
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.game_data.repair_published_gold_catalog import verify_drop_identity


def _write_table(path: Path, table_rows: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([{"Rows": table_rows}]), encoding="utf-8")


def _fixture(root: Path) -> tuple[Path, Path]:
    groups = root / "DataTable/Drop/Client/ClientDropGroupDataTable.json"
    sequences = root / "DataTable/Drop/DropSequenceDataTable.json"
    _write_table(groups, {
        "drop_paid_0": {"SequenceId": "droplist_gold"},
        "drop_fons1_0": {"SequenceId": "droplist_Fons"},
    })
    _write_table(sequences, {
        "droplist_Gold_0": {"ItemID": "Gold"},
        "droplist_Fons_0": {"ItemID": "Fons"},
    })
    return groups, sequences


def test_paid_gold_and_mammon_fons_are_distinct(tmp_path: Path) -> None:
    _fixture(tmp_path)
    result = verify_drop_identity(tmp_path, ("drop_paid",))
    assert result["gold_drop_count"] == 1
    assert result["fons_drop_id"] == "drop_fons1"


def test_paid_drop_cannot_silently_remap_real_fons(tmp_path: Path) -> None:
    groups, sequences = _fixture(tmp_path)
    _write_table(groups, {
        "drop_paid_0": {"SequenceId": "droplist_Fons"},
        "drop_fons1_0": {"SequenceId": "droplist_Fons"},
    })
    with pytest.raises(ValueError, match="货币来源冲突"):
        verify_drop_identity(tmp_path, ("drop_paid",))
    _write_table(sequences, {"droplist_Fons_0": {"ItemID": "Gold"}})
    with pytest.raises(ValueError, match="货币来源冲突"):
        verify_drop_identity(tmp_path, ())
