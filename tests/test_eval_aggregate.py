"""Tests for the evaluation aggregation artifacts."""

import json

from src.eval.aggregate import aggregate_inputs, render_markdown


def _hit(turn_id: str) -> dict:
    return {"memory": {"sources": [{"session_id": 1, "turn_id": turn_id}]}}


def test_aggregate_recomputes_metrics_over_multiple_answer_files(tmp_path):
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    first.write_text(
        json.dumps(
            [
                {
                    "answer": "Boston",
                    "gold_answer": "Boston",
                    "gold_evidence": ["D1:1"],
                    "hits": [_hit("D1:1")],
                }
            ]
        ),
        encoding="utf-8",
    )
    second.write_text(
        json.dumps(
            [
                {
                    "answer": "Paris",
                    "gold_answer": "London",
                    "gold_evidence": ["D1:2"],
                    "hits": [_hit("D1:2")],
                }
            ]
        ),
        encoding="utf-8",
    )
    summary = aggregate_inputs(
        [f"ours={first}", f"ours={second}"],
        dataset="dev",
    )
    metrics = summary["methods"]["ours"]
    assert metrics["rows"] == 2
    assert metrics["qa_f1"] == 0.5
    assert metrics["recall@1"] == 1.0
    assert metrics["source"] == "answer_rows"


def test_markdown_shows_unavailable_metrics_as_em_dash():
    summary = {
        "dataset": "dev",
        "generated_at": "2026-11-01T00:00:00+00:00",
        "methods": {
            "full_text": {
                "rows": 2,
                "qa_f1": 0.5,
                "recall@1": None,
                "recall@5": None,
                "relation_accuracy": None,
                "average_tokens": None,
                "input_files": ["answers.json"],
            }
        },
    }
    markdown = render_markdown(summary)
    assert "full_text" in markdown
    assert "—" in markdown
