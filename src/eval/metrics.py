"""Pure-Python evaluation metrics used by the course runner.

The functions in this module do not call a model or read the course dataset. They
accept JSON-ready dictionaries as well as the project's Pydantic objects, so they
can be tested offline and reused for recorded runs.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

RELATION_LABELS = ("RETAIN", "UPDATE", "SUPERSEDE", "CONFLICT")
_TOKEN_RE = re.compile(r"[a-z0-9]+|[\u3400-\u9fff]")


def normalize_tokens(text: str | None) -> list[str]:
    """Tokenize English words/numbers and individual CJK characters."""
    if not text:
        return []
    return _TOKEN_RE.findall(text.casefold())


def token_f1(prediction: str | None, reference: str | None) -> float:
    """Return the word-level F1 used by the project smoke evaluator."""
    predicted = Counter(normalize_tokens(prediction))
    expected = Counter(normalize_tokens(reference))
    overlap = sum((predicted & expected).values())
    if not predicted and not expected:
        return 1.0
    if not predicted or not expected or overlap == 0:
        return 0.0
    precision = overlap / sum(predicted.values())
    recall = overlap / sum(expected.values())
    return 2 * precision * recall / (precision + recall)


def macro_token_f1(rows: Iterable[Mapping[str, Any]]) -> float | None:
    """Average F1 over rows containing answer/prediction and reference fields."""
    scores = []
    for row in rows:
        prediction = _first(row, "answer", "prediction", "generated_answer")
        reference = _first(row, "gold_answer", "reference_answer", "expected_answer")
        if prediction is None or reference is None:
            continue
        scores.append(token_f1(str(prediction), str(reference)))
    return sum(scores) / len(scores) if scores else None


def _first(mapping: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in mapping:
            return mapping[key]
    return None


def _as_mapping(value: Any) -> Mapping[str, Any] | None:
    if isinstance(value, Mapping):
        return value
    model_dump = getattr(value, "model_dump", None)
    if model_dump is not None:
        dumped = model_dump(mode="json")
        if isinstance(dumped, Mapping):
            return dumped
    return None


def _evidence_key(value: Any) -> str | None:
    if isinstance(value, str):
        return value
    mapping = _as_mapping(value)
    if mapping is None:
        return None
    turn_id = mapping.get("turn_id")
    session_id = mapping.get("session_id")
    if turn_id is None:
        return None
    return f"{session_id}:{turn_id}" if session_id is not None else str(turn_id)


def _gold_evidence_keys(gold_evidence: Any) -> set[str]:
    if gold_evidence is None:
        return set()
    if isinstance(gold_evidence, str | Mapping) or hasattr(gold_evidence, "model_dump"):
        gold_evidence = [gold_evidence]
    keys = set()
    for item in gold_evidence:
        key = _evidence_key(item)
        if key:
            keys.add(key)
    return keys


def _hit_evidence_keys(hit: Any) -> set[str]:
    mapping = _as_mapping(hit)
    if mapping is None:
        return set()
    memory = mapping.get("memory", mapping)
    memory_mapping = _as_mapping(memory)
    if memory_mapping is None:
        return set()
    keys = set()
    for source in memory_mapping.get("sources", []):
        source_mapping = _as_mapping(source)
        if source_mapping is None:
            continue
        turn_id = source_mapping.get("turn_id")
        session_id = source_mapping.get("session_id")
        if turn_id is not None:
            keys.add(str(turn_id))
            if session_id is not None:
                keys.add(f"{session_id}:{turn_id}")
    return keys


def recall_at_k(predicted_hits: Sequence[Any], gold_evidence: Any, k: int) -> float | None:
    """Return题级 Recall@K: top K contains at least one gold evidence.

    A question without gold evidence is excluded from the denominator. Related
    history returned by ``retrieve_context`` must not be passed as hits.
    """
    if type(k) is not int or k <= 0:
        raise ValueError("k must be a positive integer")
    gold = _gold_evidence_keys(gold_evidence)
    if not gold:
        return None
    retrieved = set()
    for hit in predicted_hits[:k]:
        retrieved.update(_hit_evidence_keys(hit))
    return 1.0 if retrieved & gold else 0.0


def _row_hits(row: Mapping[str, Any]) -> Sequence[Any]:
    retrieval = row.get("retrieval")
    retrieval_mapping = _as_mapping(retrieval)
    if retrieval_mapping is not None and isinstance(retrieval_mapping.get("hits"), list):
        return retrieval_mapping["hits"]
    hits = row.get("hits", [])
    return hits if isinstance(hits, list) else []


def _row_gold_evidence(row: Mapping[str, Any]) -> Any:
    return _first(row, "gold_evidence", "evidence", "reference_evidence")


def _row_relation(row: Mapping[str, Any]) -> str | None:
    value = _first(row, "gold_relation", "reference_relation", "relation_gold")
    if value is not None:
        return str(value).upper()
    predicted = _first(row, "relation", "predicted_relation", "relation_type")
    if isinstance(predicted, Mapping):
        predicted = predicted.get("relation_type")
    return str(predicted).upper() if predicted is not None else None


def recall_summary(
    rows: Iterable[Mapping[str, Any]], ks: Sequence[int] = (1, 5)
) -> dict[str, float | None]:
    rows = list(rows)
    result: dict[str, float | None] = {}
    for k in ks:
        values = [
            value
            for row in rows
            if (value := recall_at_k(_row_hits(row), _row_gold_evidence(row), k)) is not None
        ]
        result[f"recall@{k}"] = sum(values) / len(values) if values else None
    return result


def relation_accuracy(predicted: Sequence[Any], expected: Sequence[Any]) -> float | None:
    """Calculate accuracy over the four relation labels."""
    pairs = []
    for prediction, reference in zip(predicted, expected, strict=False):
        p = _relation_value(prediction)
        e = _relation_value(reference)
        if p is not None and e is not None:
            pairs.append(p == e)
    return sum(pairs) / len(pairs) if pairs else None


def _relation_value(value: Any) -> str | None:
    mapping = _as_mapping(value)
    if mapping is not None:
        value = mapping.get("relation_type", mapping.get("relation"))
    if value is None:
        return None
    value = str(value).upper()
    return value if value in RELATION_LABELS else None


def confusion_matrix(
    predicted: Sequence[Any], expected: Sequence[Any]
) -> dict[str, dict[str, int]]:
    matrix = {gold: dict.fromkeys(RELATION_LABELS, 0) for gold in RELATION_LABELS}
    for prediction, reference in zip(predicted, expected, strict=False):
        p = _relation_value(prediction)
        e = _relation_value(reference)
        if p is not None and e is not None:
            matrix[e][p] += 1
    return matrix


def evaluate_rows(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Compute every metric that can be computed from recorded rows."""
    rows = list(rows)
    relations = [
        (row.get("relation"), _first(row, "gold_relation", "reference_relation", "relation_gold"))
        for row in rows
    ]
    relation_pairs = [(p, e) for p, e in relations if e is not None]
    predicted, expected = zip(*relation_pairs, strict=False) if relation_pairs else ([], [])
    result: dict[str, Any] = {
        "rows": len(rows),
        "qa_f1": macro_token_f1(rows),
        "relation_accuracy": relation_accuracy(predicted, expected),
        "relation_confusion_matrix": confusion_matrix(predicted, expected),
        **recall_summary(rows),
    }
    token_values = []
    for row in rows:
        value = _first(row, "token_usage", "tokens", "total_tokens")
        if isinstance(value, int | float):
            token_values.append(float(value))
    result["average_tokens"] = sum(token_values) / len(token_values) if token_values else None
    result["answer_rows"] = sum(
        _first(row, "answer", "prediction", "generated_answer") is not None for row in rows
    )
    result["evidence_rows"] = sum(
        bool(_gold_evidence_keys(_row_gold_evidence(row))) for row in rows
    )
    result["relation_rows"] = len(relation_pairs)
    return result
