from enum import Enum
from typing import Any, Iterator, List, Tuple, Union

import yaml
from pydantic import BaseModel


class ClassificationOutcome(str, Enum):
    TRUE_POSITIVE = "True Positive"
    TRUE_NEGATIVE = "True Negative"
    FALSE_POSITIVE = "False Positive"
    FALSE_NEGATIVE = "False Negative"


class AggregationMethod(Enum):
    MACRO = "macro"
    MICRO = "micro"
    WEIGHTED = "weighted"


class ClassificationMetrics(BaseModel):
    precision: float
    recall: float
    f1_score: float
    accuracy: float
    specificity: float


def calculate_metrics(
    outcomes: List[Union[ClassificationOutcome, Tuple[ClassificationOutcome, Any]]]
) -> ClassificationMetrics:
    outcomes = [
        outcome if isinstance(outcome, ClassificationOutcome) else outcome[0]
        for outcome in outcomes
    ]
    tp = outcomes.count(ClassificationOutcome.TRUE_POSITIVE)
    tn = outcomes.count(ClassificationOutcome.TRUE_NEGATIVE)
    fp = outcomes.count(ClassificationOutcome.FALSE_POSITIVE)
    fn = outcomes.count(ClassificationOutcome.FALSE_NEGATIVE)

    # Avoid division by zero
    precision = tp / (tp + fp) if tp + fp > 0 else 0
    recall = tp / (tp + fn) if tp + fn > 0 else 0
    f1 = 2 * (precision * recall) / (precision + recall) if precision + recall > 0 else 0
    accuracy = (tp + tn) / (tp + tn + fp + fn) if tp + tn + fp + fn > 0 else 0
    specificity = tn / (tn + fp) if tn + fp > 0 else 0

    return ClassificationMetrics(
        precision=precision, recall=recall, f1_score=f1, accuracy=accuracy, specificity=specificity
    )


def _normalize(obj: Any) -> str:
    if isinstance(obj, str):
        return obj
    elif isinstance(obj, dict):
        return yaml.safe_dump(obj, sort_keys=True)
    elif isinstance(obj, list):
        return yaml.safe_dump(obj, sort_keys=True)
    else:
        return str(obj)


def evaluate_predictions(obj1: Any, obj2: Any) -> Iterator[Tuple[ClassificationOutcome, str]]:
    if isinstance(obj1, list) and isinstance(obj2, list):
        set1 = {_normalize(obj) for obj in obj1}
        set2 = {_normalize(obj) for obj in obj2}
        for x in set1.union(set2):
            if x not in set1:
                yield ClassificationOutcome.FALSE_NEGATIVE, f"{x} in {set2}"
            elif x not in set2:
                yield ClassificationOutcome.FALSE_POSITIVE, f"{x} in {set1}"
            else:
                yield ClassificationOutcome.TRUE_POSITIVE, f"{x} in both"
    else:
        yield from evaluate_predictions([obj1], [obj2])


def aggregate_metrics(
    metrics_list: List[ClassificationMetrics], method: AggregationMethod = AggregationMethod.MACRO
):
    if method == AggregationMethod.MACRO:
        return ClassificationMetrics(
            precision=sum(m.precision for m in metrics_list) / len(metrics_list),
            recall=sum(m.recall for m in metrics_list) / len(metrics_list),
            f1_score=sum(m.f1_score for m in metrics_list) / len(metrics_list),
            accuracy=sum(m.accuracy for m in metrics_list) / len(metrics_list),
            specificity=sum(m.specificity for m in metrics_list) / len(metrics_list),
        )
    elif method == AggregationMethod.MICRO:
        total_tp = sum(m.precision * (m.recall * (m.precision + m.f1_score)) for m in metrics_list)
        total_fp = sum(m.f1_score - m.precision * m.recall for m in metrics_list)
        total_fn = sum((1 - m.recall) * (m.precision + m.f1_score) for m in metrics_list)
        total_tn = sum(
            m.accuracy * (m.precision + m.recall + m.f1_score + 1) - total_tp - total_fp - total_fn
            for m in metrics_list
        )

        precision = total_tp / (total_tp + total_fp)
        recall = total_tp / (total_tp + total_fn)
        f1_score = 2 * (precision * recall) / (precision + recall)
        accuracy = (total_tp + total_tn) / (total_tp + total_tn + total_fp + total_fn)
        specificity = total_tn / (total_tn + total_fp)

        return ClassificationMetrics(
            precision=precision,
            recall=recall,
            f1_score=f1_score,
            accuracy=accuracy,
            specificity=specificity,
        )
    elif method == AggregationMethod.WEIGHTED:
        total_weight = sum(
            m.precision + m.recall + m.f1_score + m.accuracy + m.specificity for m in metrics_list
        )
        return ClassificationMetrics(
            precision=sum(
                m.precision * (m.precision + m.recall + m.f1_score + m.accuracy + m.specificity)
                for m in metrics_list
            )
            / total_weight,
            recall=sum(
                m.recall * (m.precision + m.recall + m.f1_score + m.accuracy + m.specificity)
                for m in metrics_list
            )
            / total_weight,
            f1_score=sum(
                m.f1_score * (m.precision + m.recall + m.f1_score + m.accuracy + m.specificity)
                for m in metrics_list
            )
            / total_weight,
            accuracy=sum(
                m.accuracy * (m.precision + m.recall + m.f1_score + m.accuracy + m.specificity)
                for m in metrics_list
            )
            / total_weight,
            specificity=sum(
                m.specificity * (m.precision + m.recall + m.f1_score + m.accuracy + m.specificity)
                for m in metrics_list
            )
            / total_weight,
        )
    else:
        raise ValueError("Invalid aggregation method")
