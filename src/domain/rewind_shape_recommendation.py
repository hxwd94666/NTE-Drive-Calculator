# 定义倒带形状推荐的领域输入、结果与分配规则。
"""Pure ranking for the custom rewind shape selector.

The first release is intentionally a generic inventory recommendation: it
balances the shape requirements of every currently shipped suit against the
player's fixed inventory snapshot.  A later profile-aware policy can replace
the input demand without changing the UI or service boundary.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RewindShape:
    """One official drive geometry and its grid area."""

    shape_id: str
    cell_count: int


@dataclass(frozen=True, slots=True)
class RewindShapeRecommendation:
    """One selectable shape with its explainable generic priority."""

    shape: RewindShape
    suit_demand: int
    owned_count: int
    priority_score: float
    quantity: int = 1
    quality_gap: float = 0.0


@dataclass(frozen=True, slots=True)
class RewindPricingRule:
    """Exact custom-pool cost and probability rule for intermediate/advanced."""

    base_cost: float = 10.0
    repeat_increment: float = 5.0
    slot_probability: float = 0.125
    currency: str = "萝卜币"

    def unit_cost_for_quantity(self, quantity: int) -> float:
        """Unit price shared by every slot of the same repeated shape."""

        return self.base_cost + self.repeat_increment * max(0, quantity - 1)

    def cost_for_quantity(self, quantity: int) -> float:
        count = max(0, quantity)
        return count * self.unit_cost_for_quantity(count)

    def probability_for_quantity(self, quantity: int) -> float:
        return min(1.0, max(0, quantity) * self.slot_probability)


@dataclass(frozen=True, slots=True)
class RewindPlan:
    """One eight-slot multiset, including score benefit and exact token cost."""

    key: str
    label: str
    recommendations: tuple[RewindShapeRecommendation, ...]
    score_benefit: float
    total_cost: float

    @property
    def relative_cost(self) -> float:
        """Compatibility alias for previously persisted/displayed prototype data."""

        return self.total_cost


def recommend_rewind_shapes(
    *,
    shapes: tuple[RewindShape, ...],
    required_shape_ids: tuple[str, ...],
    owned_shape_counts: Counter[str],
    selection_limit: int = 8,
) -> tuple[RewindShapeRecommendation, ...]:
    """Return exactly the most under-supplied selectable shape types.

    ``suit_demand / (owned + 1)`` favours shapes used by many suits while
    making a missing shape decisively more valuable than another surplus copy.
    Deterministic tie-breaking keeps a pinned snapshot reproducible.
    """

    if selection_limit <= 0:
        raise ValueError("selection_limit must be positive")

    demand = Counter(required_shape_ids)
    candidates = tuple(
        RewindShapeRecommendation(
            shape=shape,
            suit_demand=demand[shape.shape_id],
            owned_count=max(0, int(owned_shape_counts.get(shape.shape_id, 0))),
            priority_score=demand[shape.shape_id]
            / (max(0, int(owned_shape_counts.get(shape.shape_id, 0))) + 1),
        )
        for shape in shapes
    )
    ordered = sorted(
        candidates,
        key=lambda row: (
            -row.priority_score,
            -row.suit_demand,
            row.owned_count,
            row.shape.shape_id,
        ),
    )
    return tuple(ordered[:selection_limit])


def recommend_rewind_shape_quantities(
    *,
    shapes: tuple[RewindShape, ...],
    shape_demand: Counter[str],
    owned_shape_counts: Counter[str],
    selection_limit: int = 8,
    consider_inventory_gap: bool = True,
) -> tuple[RewindShapeRecommendation, ...]:
    """Allocate all rewind slots, allowing the same shape to be chosen again.

    Every extra copy has a diminishing marginal value.  This provides a stable
    first-pass multiset recommendation before the role-specific score model is
    connected: an under-supplied, high-demand shape may occupy several of the
    eight game slots instead of being artificially limited to one.
    """

    if selection_limit <= 0:
        raise ValueError("selection_limit must be positive")

    selected: Counter[str] = Counter()
    first_scores: dict[str, float] = {}
    shape_by_id = {shape.shape_id: shape for shape in shapes}
    for _slot in range(selection_limit):
        def marginal_score(shape: RewindShape) -> float:
            owned = max(0, int(owned_shape_counts.get(shape.shape_id, 0)))
            selected_count = selected[shape.shape_id]
            denominator = selected_count + 1
            if consider_inventory_gap:
                denominator += owned
            return shape_demand[shape.shape_id] / denominator

        winner = max(
            shapes,
            key=lambda shape: (marginal_score(shape), shape_demand[shape.shape_id], shape.shape_id),
        )
        first_scores.setdefault(winner.shape_id, marginal_score(winner))
        selected[winner.shape_id] += 1

    return tuple(
        RewindShapeRecommendation(
            shape=shape_by_id[shape_id],
            suit_demand=shape_demand[shape_id],
            owned_count=max(0, int(owned_shape_counts.get(shape_id, 0))),
            priority_score=first_scores[shape_id],
            quantity=quantity,
        )
        for shape_id, quantity in sorted(
            selected.items(),
            key=lambda item: (-first_scores[item[0]], -shape_demand[item[0]], item[0]),
        )
    )


def recommend_rewind_plans(
    *,
    shapes: tuple[RewindShape, ...],
    shape_demand: Counter[str],
    owned_shape_counts: Counter[str],
    quality_gaps: Counter[str],
    pricing_rule: RewindPricingRule,
    selection_limit: int = 8,
) -> tuple[RewindPlan, ...]:
    """Solve all eight-slot multisets for score, balance and economy variants.

    A shape's value combines role-specific high-quality-stock shortage with
    ordinary tape demand.  Repeats are evaluated as a complete multiset, so
    the second/third copy pays its own escalating price instead of merely
    receiving a greedy-count penalty.
    """

    if selection_limit <= 0:
        raise ValueError("selection_limit must be positive")
    ordered_shapes = tuple(sorted(shapes, key=lambda row: row.shape_id))
    if not ordered_shapes:
        return ()

    distributions = tuple(_shape_distributions(len(ordered_shapes), selection_limit))
    variants = (
        ("score", "冲分方案", 0.02),
        ("balanced", "综合方案", 0.14),
        ("economy", "省币方案", 0.55),
    )
    plans: list[RewindPlan] = []
    for key, label, cost_weight in variants:
        best = max(
            distributions,
            key=lambda counts: _plan_objective(
                counts,
                ordered_shapes,
                shape_demand,
                owned_shape_counts,
                quality_gaps,
                pricing_rule,
                cost_weight,
            ),
        )
        benefit, cost = _plan_metrics(
            best,
            ordered_shapes,
            shape_demand,
            owned_shape_counts,
            quality_gaps,
            pricing_rule,
        )
        rows = tuple(
            RewindShapeRecommendation(
                shape=shape,
                suit_demand=int(shape_demand[shape.shape_id]),
                owned_count=max(0, int(owned_shape_counts.get(shape.shape_id, 0))),
                priority_score=_shape_value(shape, shape_demand, quality_gaps),
                quantity=quantity,
                quality_gap=float(quality_gaps.get(shape.shape_id, 0.0)),
            )
            for shape, quantity in zip(ordered_shapes, best)
            if quantity
        )
        plans.append(
            RewindPlan(
                key=key,
                label=label,
                recommendations=rows,
                score_benefit=round(benefit, 2),
                total_cost=round(cost, 2),
            )
        )
    return tuple(plans)


def _shape_distributions(shape_count: int, total: int):
    if shape_count == 1:
        yield (total,)
        return
    for count in range(total + 1):
        for remainder in _shape_distributions(shape_count - 1, total - count):
            yield (count, *remainder)


def _shape_value(
    shape: RewindShape,
    shape_demand: Counter[str],
    quality_gaps: Counter[str],
) -> float:
    return float(shape_demand[shape.shape_id]) + float(quality_gaps[shape.shape_id]) * 6.0


def _plan_metrics(
    counts: tuple[int, ...],
    shapes: tuple[RewindShape, ...],
    shape_demand: Counter[str],
    owned_shape_counts: Counter[str],
    quality_gaps: Counter[str],
    pricing_rule: RewindPricingRule,
) -> tuple[float, float]:
    benefit = 0.0
    cost = 0.0
    for shape, quantity in zip(shapes, counts):
        value = _shape_value(shape, shape_demand, quality_gaps)
        owned = max(0, int(owned_shape_counts.get(shape.shape_id, 0)))
        for copy_index in range(quantity):
            benefit += value / (1.0 + owned * 0.12 + copy_index * 0.38)
        cost += pricing_rule.cost_for_quantity(quantity)
    return benefit, cost


def _plan_objective(
    counts: tuple[int, ...],
    shapes: tuple[RewindShape, ...],
    shape_demand: Counter[str],
    owned_shape_counts: Counter[str],
    quality_gaps: Counter[str],
    pricing_rule: RewindPricingRule,
    cost_weight: float,
) -> tuple[float, float, tuple[int, ...]]:
    benefit, cost = _plan_metrics(
        counts,
        shapes,
        shape_demand,
        owned_shape_counts,
        quality_gaps,
        pricing_rule,
    )
    # Final deterministic tie-break favours a less concentrated distribution.
    concentration = -sum(quantity * quantity for quantity in counts)
    return (benefit - cost * cost_weight, concentration, counts)


GRADE_RATIOS = {"D": 0.0, "C": 0.2, "B": 0.3, "A": 0.4, "S": 0.5, "SS": 0.6, "SSS": 0.7, "ACE": 0.8}


def target_grade_score(grade: str, area: int) -> float:
    """Return the per-drive score threshold used by rewind target modes."""

    return GRADE_RATIOS.get(str(grade).upper(), GRADE_RATIOS["S"]) * max(1, area) * 10.0


def _normalized_integer_ratios(priority_values: dict[str, float]) -> dict[str, int]:
    """Normalize positive priorities so the smallest ratio is one integer unit."""

    positive = {shape_id: float(value) for shape_id, value in priority_values.items() if value > 0}
    if not positive:
        return {shape_id: 1 for shape_id in priority_values}
    smallest = min(positive.values())
    return {
        shape_id: max(1, int(value / smallest)) if value > 0 else 1
        for shape_id, value in priority_values.items()
    }


def _allocate_ratio_repeats(
    selected: Counter[str],
    *,
    priority_values: dict[str, float],
    selection_limit: int,
) -> Counter[str]:
    """Apply the documented ratio allocation after every shape has a base slot.

    The priority ratios are normalized so their minimum is one, then converted
    to integers.  That one unit is the mandatory base seat; only the residual
    ratio participates in the proportional allocation of the remaining seats.
    Floor the proportional quotas and give *all* unfilled seats to the largest
    original ratio, exactly matching the eight-slot custom-pool rule.
    """

    remaining = max(0, int(selection_limit) - sum(selected.values()))
    if remaining <= 0 or not selected:
        return selected
    priorities = {shape_id: float(priority_values.get(shape_id, 0.0)) for shape_id in selected}
    ratios = _normalized_integer_ratios(priorities)
    residual = {shape_id: max(0, ratio - 1) for shape_id, ratio in ratios.items()}
    residual_total = sum(residual.values())
    additions = {shape_id: 0 for shape_id in selected}
    if residual_total:
        additions = {
            shape_id: int(remaining * value / residual_total)
            for shape_id, value in residual.items()
        }
        for shape_id, count in additions.items():
            selected[shape_id] += count
    leftover = remaining - sum(additions.values())
    if leftover:
        winner = max(
            selected,
            key=lambda shape_id: (ratios[shape_id], priorities[shape_id], shape_id),
        )
        selected[winner] += leftover
    return selected


def _reciprocal_stock_priorities(
    shape_ids: tuple[str, ...],
    owned_shape_counts: Counter[str],
) -> dict[str, float]:
    """Use finite reciprocal stock priorities; zero stock shares top priority."""

    return {
        shape_id: 1.0 / max(1, int(owned_shape_counts.get(shape_id, 0)))
        for shape_id in shape_ids
    }
def recommend_score_shortfall_shapes(
    *,
    shapes: tuple[RewindShape, ...],
    owned_shape_counts: Counter[str],
    shortfalls: Counter[str],
    score_gaps: Counter[str],
    selection_limit: int = 8,
    proportional: bool = True,
) -> tuple[RewindShapeRecommendation, ...]:
    """Fill every under-grade drive, then allocate repeats by the selected mode."""
    required = sum(max(0, int(value)) for value in shortfalls.values())
    if required > selection_limit:
        return ()
    selected: Counter[str] = Counter({key: int(value) for key, value in shortfalls.items() if value > 0})
    by_id = {shape.shape_id: shape for shape in shapes}
    if proportional:
        while sum(selected.values()) < selection_limit and selected:
            winner = max(
                selected,
                key=lambda shape_id: (
                    score_gaps[shape_id] / (selected[shape_id] + 1),
                    score_gaps[shape_id],
                    shape_id,
                ),
            )
            selected[winner] += 1
    else:
        # Comprehensive mode combines quality shortage with current stock:
        # a shape's repeat priority is total score gap × reciprocal inventory.
        _allocate_ratio_repeats(
            selected,
            priority_values={
                shape_id: float(score_gaps[shape_id]) / max(1, int(owned_shape_counts[shape_id]))
                for shape_id in selected
            },
            selection_limit=selection_limit,
        )
    return tuple(
        RewindShapeRecommendation(
            shape=by_id[shape_id],
            suit_demand=int(shortfalls[shape_id]),
            owned_count=int(owned_shape_counts[shape_id]),
            priority_score=float(score_gaps[shape_id]),
            quantity=quantity,
            quality_gap=float(score_gaps[shape_id]),
        )
        for shape_id, quantity in sorted(selected.items(), key=lambda item: (-score_gaps[item[0]], item[0]))
    )
