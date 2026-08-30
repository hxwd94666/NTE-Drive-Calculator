# 保留 feature 公共导入路径，唯一实现由合法的 Service 投影层拥有。
"""Public formula-detail exports for the static-catalog feature."""

from src.services.static_catalog_formula_presenters import (
    FormulaDetailSectionView,
    FormulaDetailView,
    FormulaSourceView,
    build_formula_detail_sections,
)

__all__ = [
    "FormulaDetailSectionView",
    "FormulaDetailView",
    "FormulaSourceView",
    "build_formula_detail_sections",
]
