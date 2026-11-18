# 验证界面中文字面量只减不增：新增硬编码文案须经文本目录渲染或登记，遗留基线只能下降。
from __future__ import annotations

from tools.quality import check_ui_text_coverage as coverage

NTE_TEST_TIER = "core"


def test_scanner_ignores_catalog_logs_comparisons_and_docstrings(tmp_path):
    source = tmp_path / "page.py"
    source.write_text(
        '"""模块说明不算界面文案。"""\n'
        'import logging\n'
        'logger = logging.getLogger(__name__)\n'
        'def show(label, catalog, raw):\n'
        '    logger.warning("日志不翻译")\n'
        '    if raw == "运行中":\n'
        '        label.setText(localized_text(catalog, "x.y", "兜底中文"))\n'
        '    label.setText("新增的硬编码中文")\n',
        encoding="utf-8",
    )
    assert [text for _, text in coverage.scan_file(source)] == ["新增的硬编码中文"]


def test_registered_catalog_text_counts_as_covered(tmp_path):
    registered = next(iter(sorted(coverage.catalog_texts(), key=len, reverse=True)))
    source = tmp_path / "table.py"
    source.write_text(f"LABELS = ({registered!r},)\n", encoding="utf-8")
    assert coverage.scan_file(source) == []


def test_new_literals_beyond_baseline_are_reported():
    scan = {"src/features/demo.py": [(1, "甲"), (2, "乙")], "src/features/new.py": [(3, "丙")]}
    problems = coverage.regressions(scan, {"src/features/demo.py": 2})
    assert len(problems) == 1 and problems[0].startswith("src/features/new.py")


def test_project_does_not_exceed_legacy_baseline():
    # 遗留缺口按文件计数只减不增；清理后用 --write-baseline 收紧基线，不得调高。
    problems = coverage.regressions(coverage.scan_project(), coverage.load_baseline())
    assert problems == [], "\n".join(problems)


def test_baseline_only_lists_existing_files():
    root = coverage.PROJECT_ROOT
    stale = [rel for rel in coverage.load_baseline() if not (root / rel).is_file()]
    assert stale == [], f"基线中有已删除的文件，请重写基线：{stale}"
