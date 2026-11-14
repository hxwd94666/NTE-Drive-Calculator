# 提供测试冻结的发行身份，区分随包实际 schema 与源码支持的最大 schema。
import json
from pathlib import Path


def distribution_manifest():
    return json.loads((Path(__file__).resolve().parents[2] / "data/manifest.json").read_text(encoding="utf-8"))
