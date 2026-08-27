# 用同一批逐击的未校正公式预测，为严格遭遇候选建立确定性鲁棒残差排序。
"""Pure robust scoring for strict encounter candidates with conflicting profiles."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from math import exp, isclose, isfinite, log, log1p
from typing import Final


BATTLE_ENCOUNTER_FIT_ALGORITHM_VERSION: Final = "battle-encounter-robust-fit-v1"
_STUDENT_T_DEGREES_OF_FREEDOM: Final = 4.0
_LOG_RESIDUAL_SCALE: Final = 0.05
_DAMAGE_OFFSET: Final = 0.5
_TIE_TOLERANCE: Final = 1e-12


@dataclass(frozen=True, slots=True)
class BattleEncounterFitPrediction:
    """One candidate's raw formula predictions for one observed hit.

    ``corrected_expected_damage`` is intentionally absent: a value corrected
    by this hit's observed damage would cancel the target multiplier that the
    encounter fit is supposed to distinguish.
    """

    event_id: str
    observed_damage: float
    non_critical_damage: float | None = None
    critical_damage: float | None = None
    expected_damage: float | None = None
    group_id: str = ""
    evidence_weight: float = 1.0


@dataclass(frozen=True, slots=True)
class BattleEncounterFitCandidate:
    candidate_ref: str
    predictions: tuple[BattleEncounterFitPrediction, ...]


@dataclass(frozen=True, slots=True)
class BattleEncounterHitFitAudit:
    event_id: str
    group_id: str
    observed_damage: float | None
    non_critical_damage: float | None
    critical_damage: float | None
    expected_damage: float | None
    eligible: bool
    prediction_mode: str
    robust_loss: float | None
    critical_probability: float | None = None
    non_critical_log_residual: float | None = None
    critical_log_residual: float | None = None
    exclusion_reason: str = ""


@dataclass(frozen=True, slots=True)
class BattleEncounterCandidateFitScore:
    candidate_ref: str
    robust_score: float
    used_hit_count: int
    used_group_count: int
    excluded_hit_count: int
    hit_audits: tuple[BattleEncounterHitFitAudit, ...]


@dataclass(frozen=True, slots=True)
class BattleEncounterFitSelection:
    winner_ref: str
    scores: tuple[BattleEncounterCandidateFitScore, ...]
    score_gap: float
    relative_score_gap: float
    confidence: str
    selection_mode: str
    ambiguous: bool
    alternatives: tuple[str, ...]
    audit_summary: str
    algorithm_version: str = BATTLE_ENCOUNTER_FIT_ALGORITHM_VERSION
    prediction_basis: str = "raw_replay_predictions_without_observed_correction"


@dataclass(frozen=True, slots=True)
class _HitLoss:
    mode: str
    loss: float
    critical_probability: float | None
    non_critical_log_residual: float | None
    critical_log_residual: float | None


def _positive(value: float | None) -> float | None:
    if value is None:
        return None
    number = float(value)
    return number if number > 0.0 and isfinite(number) else None


def _log_residual(observed: float, predicted: float) -> float:
    return log((observed + _DAMAGE_OFFSET) / (predicted + _DAMAGE_OFFSET))


def _student_t_kernel_loss(residual: float) -> float:
    scaled = residual / _LOG_RESIDUAL_SCALE
    return (
        (_STUDENT_T_DEGREES_OF_FREEDOM + 1.0)
        / 2.0
        * log1p(scaled * scaled / _STUDENT_T_DEGREES_OF_FREEDOM)
    )


def _mixture_loss(
    non_critical_loss: float,
    critical_loss: float,
    critical_probability: float,
) -> float:
    terms = []
    if critical_probability < 1.0:
        terms.append(log(1.0 - critical_probability) - non_critical_loss)
    if critical_probability > 0.0:
        terms.append(log(critical_probability) - critical_loss)
    peak = max(terms)
    return -(peak + log(sum(exp(value - peak) for value in terms)))


def _prediction_loss(
    prediction: BattleEncounterFitPrediction,
) -> tuple[_HitLoss | None, str]:
    observed = _positive(prediction.observed_damage)
    if observed is None:
        return None, "观测伤害不是有限正数"
    non_critical = _positive(prediction.non_critical_damage)
    critical = _positive(prediction.critical_damage)
    expected = _positive(prediction.expected_damage)
    if non_critical is not None and critical is not None:
        if isclose(non_critical, critical, rel_tol=1e-12, abs_tol=1e-12):
            residual = _log_residual(observed, non_critical)
            return _HitLoss(
                mode="single_branch",
                loss=_student_t_kernel_loss(residual),
                critical_probability=None,
                non_critical_log_residual=residual,
                critical_log_residual=residual,
            ), ""
        if expected is None:
            return None, "暴击双分支缺少未校正 expected_damage，无法冻结混合概率"
        probability = (expected - non_critical) / (critical - non_critical)
        if probability < -1e-9 or probability > 1.0 + 1e-9:
            return None, "expected_damage 不在未暴击/暴击原始预测之间"
        probability = min(1.0, max(0.0, probability))
        non_critical_residual = _log_residual(observed, non_critical)
        critical_residual = _log_residual(observed, critical)
        return _HitLoss(
            mode="critical_mixture",
            loss=_mixture_loss(
                _student_t_kernel_loss(non_critical_residual),
                _student_t_kernel_loss(critical_residual),
                probability,
            ),
            critical_probability=probability,
            non_critical_log_residual=non_critical_residual,
            critical_log_residual=critical_residual,
        ), ""
    predicted = non_critical or critical or expected
    if predicted is None:
        return None, "缺少有限正数的原始公式预测"
    residual = _log_residual(observed, predicted)
    return _HitLoss(
        mode=(
            "non_critical_only"
            if non_critical is not None
            else "critical_only"
            if critical is not None
            else "expected_only"
        ),
        loss=_student_t_kernel_loss(residual),
        critical_probability=None,
        non_critical_log_residual=(residual if non_critical is not None else None),
        critical_log_residual=(residual if critical is not None else None),
    ), ""


def _stable_text(value: str) -> tuple[str, str]:
    return value.casefold(), value


class BattleEncounterFitService:
    """Score every strict candidate on the same eligible hit and group set."""

    @classmethod
    def select(
        cls,
        candidates: tuple[BattleEncounterFitCandidate, ...],
    ) -> BattleEncounterFitSelection:
        if not candidates:
            raise ValueError("encounter fit requires at least one strict candidate")
        candidate_refs = [str(row.candidate_ref or "").strip() for row in candidates]
        if any(not value for value in candidate_refs):
            raise ValueError("encounter fit candidate_ref must not be empty")
        if len(set(candidate_refs)) != len(candidate_refs):
            raise ValueError("encounter fit candidate_ref must be unique")
        stable_candidates = tuple(sorted(
            candidates,
            key=lambda row: _stable_text(row.candidate_ref),
        ))
        maps: dict[str, dict[str, BattleEncounterFitPrediction]] = {}
        duplicate_ids: set[str] = set()
        for candidate in stable_candidates:
            rows: dict[str, BattleEncounterFitPrediction] = {}
            for prediction in candidate.predictions:
                event_id = str(prediction.event_id or "").strip()
                if not event_id or event_id in rows:
                    duplicate_ids.add(event_id or "<empty>")
                    continue
                rows[event_id] = prediction
            maps[candidate.candidate_ref] = rows

        all_event_ids = tuple(sorted(
            {event_id for rows in maps.values() for event_id in rows} | duplicate_ids,
            key=_stable_text,
        ))
        exclusions: dict[str, str] = {}
        losses: dict[tuple[str, str], _HitLoss] = {}
        for event_id in all_event_ids:
            if event_id in duplicate_ids:
                exclusions[event_id] = "至少一个候选重复或缺失 event_id"
                continue
            predictions = [maps[row.candidate_ref].get(event_id) for row in stable_candidates]
            if any(row is None for row in predictions):
                exclusions[event_id] = "该逐击并非所有候选都提供，已从全部候选共同排除"
                continue
            concrete = tuple(row for row in predictions if row is not None)
            observed = tuple(float(row.observed_damage) for row in concrete)
            if any(
                not isfinite(value) or value <= 0.0
                for value in observed
            ) or any(
                not isclose(observed[0], value, rel_tol=1e-9, abs_tol=1e-6)
                for value in observed[1:]
            ):
                exclusions[event_id] = "候选间观测伤害不一致或不是有限正数"
                continue
            group_ids = tuple(str(row.group_id or event_id) for row in concrete)
            weights = tuple(float(row.evidence_weight) for row in concrete)
            if len(set(group_ids)) != 1 or any(
                not isfinite(value) or value <= 0.0 for value in weights
            ) or any(
                not isclose(weights[0], value, rel_tol=1e-9, abs_tol=1e-12)
                for value in weights[1:]
            ):
                exclusions[event_id] = "候选间证据分组或权重不一致"
                continue
            candidate_losses = []
            failure = ""
            for candidate, prediction in zip(
                stable_candidates,
                concrete,
                strict=True,
            ):
                hit_loss, reason = _prediction_loss(prediction)
                if hit_loss is None:
                    failure = f"候选 {candidate.candidate_ref}：{reason}"
                    break
                candidate_losses.append((candidate.candidate_ref, hit_loss))
            if failure:
                exclusions[event_id] = failure + "；已从全部候选共同排除"
                continue
            for candidate_ref, hit_loss in candidate_losses:
                losses[(candidate_ref, event_id)] = hit_loss

        scores = tuple(
            cls._candidate_score(
                candidate,
                maps[candidate.candidate_ref],
                all_event_ids,
                exclusions,
                losses,
            )
            for candidate in stable_candidates
        )
        ranked = tuple(sorted(
            scores,
            key=lambda row: (row.robust_score, *_stable_text(row.candidate_ref)),
        ))
        winner = ranked[0]
        runner_up = ranked[1] if len(ranked) > 1 else None
        score_gap = (
            0.0
            if runner_up is None
            else max(0.0, runner_up.robust_score - winner.robust_score)
        )
        relative_gap = (
            0.0
            if runner_up is None or runner_up.robust_score <= _TIE_TOLERANCE
            else score_gap / runner_up.robust_score
        )
        common_groups = min(row.used_group_count for row in ranked)
        common_hits = min(row.used_hit_count for row in ranked)
        if runner_up is None:
            selection_mode = "unique_hard"
            confidence = "高"
        elif common_hits == 0 or score_gap <= _TIE_TOLERANCE:
            selection_mode = "ambiguous_default"
            confidence = "低"
        else:
            selection_mode = "robust_fit"
            confidence = (
                "中"
                if common_groups >= 3 and relative_gap >= 0.20
                else "低"
            )
        audit_summary = (
            f"共同合格逐击 {common_hits} 条、独立证据组 {common_groups} 个；"
            "仅使用 non_critical_damage / critical_damage / expected_damage "
            "原始公式预测的对数 Student-t 核残差，"
            "corrected_expected_damage 未进入评分。"
        )
        if len(ranked) > 1:
            audit_summary += (
                f" 同血量公式画像冲突仍保留；胜者 {winner.candidate_ref}，"
                f"与次优分差 {score_gap:.6f}、相对分差 {relative_gap:.2%}，"
                f"置信度{confidence}。"
            )
        return BattleEncounterFitSelection(
            winner_ref=winner.candidate_ref,
            scores=tuple(sorted(scores, key=lambda row: _stable_text(row.candidate_ref))),
            score_gap=score_gap,
            relative_score_gap=relative_gap,
            confidence=confidence,
            selection_mode=selection_mode,
            ambiguous=len(ranked) > 1,
            alternatives=tuple(
                row.candidate_ref for row in ranked[1:]
            ),
            audit_summary=audit_summary,
        )

    @staticmethod
    def _candidate_score(
        candidate: BattleEncounterFitCandidate,
        predictions: dict[str, BattleEncounterFitPrediction],
        all_event_ids: tuple[str, ...],
        exclusions: dict[str, str],
        losses: dict[tuple[str, str], _HitLoss],
    ) -> BattleEncounterCandidateFitScore:
        audits = []
        groups: dict[str, list[tuple[float, float]]] = defaultdict(list)
        for event_id in all_event_ids:
            prediction = predictions.get(event_id)
            reason = exclusions.get(event_id, "")
            hit_loss = losses.get((candidate.candidate_ref, event_id))
            group_id = (
                event_id
                if prediction is None
                else str(prediction.group_id or event_id)
            )
            if prediction is None or hit_loss is None:
                audits.append(BattleEncounterHitFitAudit(
                    event_id=event_id,
                    group_id=group_id,
                    observed_damage=(
                        None if prediction is None else prediction.observed_damage
                    ),
                    non_critical_damage=(
                        None if prediction is None else prediction.non_critical_damage
                    ),
                    critical_damage=(
                        None if prediction is None else prediction.critical_damage
                    ),
                    expected_damage=(
                        None if prediction is None else prediction.expected_damage
                    ),
                    eligible=False,
                    prediction_mode="excluded",
                    robust_loss=None,
                    exclusion_reason=reason or "候选未提供该逐击",
                ))
                continue
            weight = float(prediction.evidence_weight)
            groups[group_id].append((hit_loss.loss, weight))
            audits.append(BattleEncounterHitFitAudit(
                event_id=event_id,
                group_id=group_id,
                observed_damage=prediction.observed_damage,
                non_critical_damage=prediction.non_critical_damage,
                critical_damage=prediction.critical_damage,
                expected_damage=prediction.expected_damage,
                eligible=True,
                prediction_mode=hit_loss.mode,
                robust_loss=hit_loss.loss,
                critical_probability=hit_loss.critical_probability,
                non_critical_log_residual=hit_loss.non_critical_log_residual,
                critical_log_residual=hit_loss.critical_log_residual,
            ))
        group_losses = tuple(
            sum(loss * weight for loss, weight in rows)
            / sum(weight for _loss, weight in rows)
            for _group_id, rows in sorted(groups.items(), key=lambda row: _stable_text(row[0]))
        )
        robust_score = (
            sum(group_losses) / len(group_losses) if group_losses else 0.0
        )
        used_hits = sum(1 for row in audits if row.eligible)
        return BattleEncounterCandidateFitScore(
            candidate_ref=candidate.candidate_ref,
            robust_score=robust_score,
            used_hit_count=used_hits,
            used_group_count=len(group_losses),
            excluded_hit_count=len(audits) - used_hits,
            hit_audits=tuple(audits),
        )
