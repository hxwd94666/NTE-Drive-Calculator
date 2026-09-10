# 联合匹配当前配装槽位和前序同分预留槽位，保留完整一对一回填。
from __future__ import annotations

from collections.abc import Sequence

import numpy as np
from scipy.optimize import linear_sum_assignment


def match_reserved_group_slots(
    ranking_matrix: np.ndarray,
    profit_matrix: np.ndarray,
    drive_uids: Sequence[str],
    reservation_candidates: tuple[tuple[str, ...], ...],
    *,
    current_shapes: Sequence[str] | None = None,
    drive_shapes: Sequence[str] | None = None,
    reservation_shapes: Sequence[str] | None = None,
) -> tuple[np.ndarray, np.ndarray] | None:
    """Maximize current ranking while filling every earlier reservation row.

    Current columns retain their original order and eligibility. Additional
    reservation-only UIDs have no legal edge to a current row. Earlier rows
    have zero cost and no dummy columns, so a complete match witnesses a valid
    one-to-one return assignment without fixing any earlier choice greedily.
    Only current row/column indices are returned; input matrices are untouched.
    """

    ranking = np.asarray(ranking_matrix, dtype=float)
    profit = np.asarray(profit_matrix, dtype=float)
    if ranking.ndim != 2 or profit.shape != ranking.shape:
        raise ValueError("联合预留匹配的评分矩阵形状不一致")
    current_rows, current_columns = ranking.shape
    uids = tuple(drive_uids)
    if current_columns != len(uids) or len(set(uids)) != len(uids):
        raise ValueError("联合预留匹配的候选列必须对应唯一驱动")
    legal = profit >= 0
    if not np.isfinite(ranking[legal]).all():
        raise ValueError("联合预留匹配的合法候选评分必须为有限值")

    if current_shapes is not None or drive_shapes is not None or reservation_shapes is not None:
        if (
            current_shapes is None
            or drive_shapes is None
            or reservation_shapes is None
            or len(current_shapes) != current_rows
            or len(drive_shapes) != current_columns
            or len(reservation_shapes) != len(reservation_candidates)
        ):
            raise ValueError("按驱动类型联合匹配的形状索引不一致")
        return _match_by_shape(
            ranking, profit, uids, reservation_candidates,
            tuple(current_shapes), tuple(drive_shapes), tuple(reservation_shapes),
        )

    current_uid_set = set(uids)
    extra_uids = sorted({
        uid for candidates in reservation_candidates for uid in candidates
        if uid not in current_uid_set
    })
    all_uids = (*uids, *extra_uids)
    total_rows = current_rows + len(reservation_candidates)
    if total_rows > len(all_uids) or any(not candidates for candidates in reservation_candidates):
        return None
    uid_columns = {uid: column for column, uid in enumerate(all_uids)}
    costs = np.full((total_rows, len(all_uids)), np.inf)
    costs[:current_rows, :current_columns] = np.where(legal, -ranking, np.inf)
    for offset, candidates in enumerate(reservation_candidates):
        for uid in candidates:
            costs[current_rows + offset, uid_columns[uid]] = 0.0

    try:
        rows, columns = linear_sum_assignment(costs)
    except ValueError:
        # Costs contain only finite values and positive infinity; here SciPy's
        # failure means the required rows have no complete legal matching.
        return None
    if len(rows) != total_rows or not np.isfinite(costs[rows, columns]).all():
        return None
    selected = rows < current_rows
    return rows[selected], columns[selected]


def _match_by_shape(
    ranking: np.ndarray,
    profit: np.ndarray,
    drive_uids: tuple[str, ...],
    reservation_candidates: tuple[tuple[str, ...], ...],
    current_shapes: tuple[str, ...],
    drive_shapes: tuple[str, ...],
    reservation_shapes: tuple[str, ...],
) -> tuple[np.ndarray, np.ndarray] | None:
    """Solve independent drive-type pools, retaining global row indices.

    Blueprint slots and deferred candidates never cross a shape/type boundary.
    Splitting therefore gives the identical feasible set while replacing one
    large dense assignment with the small matrices that actually compete.
    """

    result_rows: list[int] = []
    result_columns: list[int] = []
    shapes = sorted(set(current_shapes) | set(reservation_shapes))
    for shape in shapes:
        rows = [index for index, value in enumerate(current_shapes) if value == shape]
        reserved = [
            index for index, value in enumerate(reservation_shapes) if value == shape
        ]
        columns = [index for index, value in enumerate(drive_shapes) if value == shape]
        current_uid_set = {drive_uids[index] for index in columns}
        extra_uids = sorted({
            uid for index in reserved for uid in reservation_candidates[index]
            if uid not in current_uid_set
        })
        all_uids = [*(drive_uids[index] for index in columns), *extra_uids]
        total_rows = len(rows) + len(reserved)
        if total_rows > len(all_uids) or any(not reservation_candidates[index] for index in reserved):
            return None

        costs = np.full((total_rows, len(all_uids)), np.inf)
        for local_row, source_row in enumerate(rows):
            for local_col, source_col in enumerate(columns):
                if profit[source_row, source_col] >= 0:
                    costs[local_row, local_col] = -ranking[source_row, source_col]
        uid_columns = {uid: index for index, uid in enumerate(all_uids)}
        for local_row, reservation_index in enumerate(reserved, start=len(rows)):
            for uid in reservation_candidates[reservation_index]:
                costs[local_row, uid_columns[uid]] = 0.0
        try:
            local_rows, local_columns = linear_sum_assignment(costs)
        except ValueError:
            return None
        if len(local_rows) != total_rows or not np.isfinite(costs[local_rows, local_columns]).all():
            return None
        for local_row, local_column in zip(local_rows.tolist(), local_columns.tolist()):
            if local_row >= len(rows):
                continue
            if local_column >= len(columns):
                return None
            result_rows.append(rows[local_row])
            result_columns.append(columns[local_column])
    if len(result_rows) != ranking.shape[0]:
        return None
    order = np.argsort(np.asarray(result_rows, dtype=int))
    return (
        np.asarray(result_rows, dtype=int)[order],
        np.asarray(result_columns, dtype=int)[order],
    )
