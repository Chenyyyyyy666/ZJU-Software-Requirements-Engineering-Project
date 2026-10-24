"""Offline tests for the evaluation metrics and course CLI."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from src.eval.metrics import (
    confusion_matrix,
    evaluate_rows,
    recall_at_k,
    relation_accuracy,
    token_f1,
)

ROOT = Path(__file__).resolve().parents[1]


def hit(turn_id: str, session_id: int = 1) -> dict:
    return {
        "rank": 1,
        "memory": {
            "memory_id": "m-1",
            "sources": [{"session_id": session_id, "turn_id": turn_id}],
        },
    }


def test_token_f1_is_case_insensitive_and_multilingual():
    assert token_f1("Alex passed the interview", "alex passed interview") == pytest.approx(6 / 7)
    assert token_f1("纽约", "纽约") == pytest.approx(1.0)
    assert token_f1("unrelated", "answer") == 0.0


def test_recall_at_k_uses_initial_hits_and_source_turn_ids():
    hits = [hit("D1:1"), hit("D2:1")]
    assert recall_at_k(hits, ["D2:1"], 1) == 0.0
    assert recall_at_k(hits, ["D2:1"], 2) == 1.0
    assert (
        recall_at_k([hit("D2:1", session_id=2)], [{"session_id": 2, "turn_id": "D2:1"}], 1) == 1.0
    )
    assert recall_at_k(hits, [], 5) is None


def test_relation_accuracy_and_confusion_matrix():
    predicted = ["RETAIN", "SUPERSEDE", "CONFLICT"]
    expected = ["RETAIN", "UPDATE", "CONFLICT"]
    assert relation_accuracy(predicted, expected) == pytest.approx(2 / 3)
    matrix = confusion_matrix(predicted, expected)
    assert matrix["UPDATE"]["SUPERSEDE"] == 1
    assert matrix["RETAIN"]["RETAIN"] == 1
    assert matrix["CONFLICT"]["CONFLICT"] == 1


def test_evaluate_rows_reports_only_available_metrics():
    rows = [
        {
            "answer": "Alex passed the interview",
            "gold_answer": "Alex passed interview",
            "hits": [hit("D2:1")],
            "gold_evidence": ["D2:1"],
            "relation": "SUPERSEDE",
            "gold_relation": "UPDATE",
            "token_usage": 100,
        }
    ]
    result = evaluate_rows(rows)
    assert result["qa_f1"] == pytest.approx(6 / 7)
    assert result["recall@1"] == 1.0
    assert result["relation_accuracy"] == 0.0
    assert result["average_tokens"] == 100.0


def test_cli_offline_mode_writes_rows_and_metrics(tmp_path):
    questions = tmp_path / "questions.json"
    answers = tmp_path / "answers-input.json"
    output = tmp_path / "answers.json"
    metrics = tmp_path / "metrics.json"
    questions.write_text(
        json.dumps([{"question_id": "q1", "question": "Where?", "gold_answer": "Boston"}]),
        encoding="utf-8",
    )
    answers.write_text(
        json.dumps(
            [
                {
                    "question_id": "q1",
                    "question": "Where?",
                    "answer": "Boston",
                    "gold_answer": "Boston",
                    "gold_evidence": ["D1:1"],
                    "hits": [hit("D1:1")],
                }
            ]
        ),
        encoding="utf-8",
    )
    subprocess.run(
        [
            sys.executable,
            "-m",
            "src.eval.cli",
            "--conversation",
            "demo-01",
            "--questions",
            str(questions),
            "--answers-input",
            str(answers),
            "--output",
            str(output),
            "--metrics-output",
            str(metrics),
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    written = json.loads(output.read_text(encoding="utf-8"))
    measured = json.loads(metrics.read_text(encoding="utf-8"))
    assert written[0]["answer"] == "Boston"
    assert measured["qa_f1"] == 1.0
    assert measured["recall@1"] == 1.0
