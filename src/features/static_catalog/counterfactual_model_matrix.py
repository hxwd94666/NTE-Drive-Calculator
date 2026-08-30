# 保留 feature 公共导入路径，唯一实现由合法的 Service 投影层拥有。
"""Public counterfactual-matrix exports for the static-catalog feature."""

from src.services.static_catalog_formula_presenters import (
    CounterfactualMatrixRow,
    CounterfactualMatrixView,
    build_counterfactual_model_matrix,
)

__all__ = [
    "CounterfactualMatrixRow",
    "CounterfactualMatrixView",
    "build_counterfactual_model_matrix",
]
