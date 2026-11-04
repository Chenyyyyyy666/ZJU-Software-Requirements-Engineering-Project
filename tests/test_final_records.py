"""Meaningful checks for telemetry gaps, metric denominators and frozen data."""

import json
from types import SimpleNamespace

import pytest

from src.common.json_io import digest
from src.eval import freeze
from src.eval.metrics import evaluate_rows
from src.reasoning.telemetry import capture_usage, tracked_completion


def fake_client(callback):
    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=callback)))


def test_usage_counts_all_visible_attempts_and_marks_unknown_usage():
    response = SimpleNamespace(
        model="actual-model",
        usage=SimpleNamespace(prompt_tokens=8, completion_tokens=2, total_tokens=10),
    )
    client = fake_client(lambda **kwargs: response)
    with capture_usage() as stats:
        tracked_completion(client, model="requested")
        assert stats.to_dict()["total_tokens"] == 10
        response.usage = None
        tracked_completion(client)
    result = stats.to_dict()
    assert result["requests"] == 2
    assert result["usage_missing_requests"] == 1
    assert result["total_tokens"] is None
    assert result["returned_models"] == ["actual-model"]


def test_failed_api_usage_is_unknown_and_context_is_restored():
    def fail(**kwargs):
        raise ConnectionError("simulated")

    with capture_usage() as stats, pytest.raises(ConnectionError):
        tracked_completion(fake_client(fail))
    assert stats.to_dict()["requests"] == 1
    assert stats.to_dict()["total_tokens"] is None
    with capture_usage() as fresh:
        assert fresh.to_dict()["requests"] == 0
        assert fresh.to_dict()["total_tokens"] == 0


def test_metric_denominators_match_scored_rows_and_failures_are_not_dropped():
    result = evaluate_rows(
        [
            {"answer": "Boston", "gold_answer": [None, ""]},
            {"answer": "", "gold_answer": "Boston"},
            {"answer": "Boston", "gold_answer": ["NYC", "Boston"]},
            {"gold_relation": "UPDATE", "relation": "UPDATE"},
            {"gold_relation": "UPDATE", "relation": None},
        ]
    )
    assert result["qa_rows"] == 2
    assert result["qa_f1"] == 0.5
    assert result["relation_accuracy"] == 0.5
    assert result["relation_failed_rows"] == 1


def test_dictionary_token_usage_latency_and_full_text_na():
    measured = evaluate_rows(
        [
            {
                "method": "full_text",
                "gold_evidence": ["D1:1"],
                "token_usage": {"total_tokens": 10},
                "elapsed_seconds": 2,
            },
            {
                "method": "full_text",
                "gold_evidence": ["D1:1"],
                "token_usage": {"total_tokens": None},
                "elapsed_seconds": 4,
            },
        ]
    )
    assert measured["recall@1"] is None
    assert measured["evidence_rows"] == 0
    assert measured["average_tokens"] == 10
    assert measured["token_rows"] == 1
    assert measured["average_elapsed_seconds"] == measured["median_elapsed_seconds"] == 3


def test_offline_cli_keeps_retrieval_and_telemetry_fields():
    from src.eval.cli import _record_offline_answers

    recorded = _record_offline_answers(
        [
            {
                "question": "Where?",
                "answer": "Boston",
                "gold_answer": "Boston",
                "retrieval": {"hits": []},
                "relations": [],
                "elapsed_seconds": 1,
                "token_usage": {"total_tokens": 10},
                "config_id": "example",
            }
        ],
        "conv-x",
    )
    assert recorded[0]["retrieval"] == {"hits": []}
    assert recorded[0]["elapsed_seconds"] == 1
    assert recorded[0]["config_id"] == "example"


def frozen_fixture(tmp_path, monkeypatch):
    root = tmp_path / "project"
    (root / "src").mkdir(parents=True)
    (root / "experiments").mkdir()
    (root / "src/module.py").write_text("VERSION = 1\n")
    (root / "pyproject.toml").write_text("")
    data = root / "data.json"
    data.write_text("[]")
    splits = root / "splits.json"
    splits.write_text('{"dev":["dev"],"test":["test1","test2"]}')
    memory = root / "memory"
    memory.mkdir()
    for conv in ["dev", "test1", "test2"]:
        (memory / f"{conv}.json").write_text(
            json.dumps({"conversation_id": conv, "build_info": {"build_id": f"build-{conv}"}})
        )
    monkeypatch.setattr(freeze, "ROOT", root)
    current = freeze.snapshot(data, memory, splits)
    record = root / "freeze.json"
    record.write_text(json.dumps({"snapshot": current, "snapshot_sha256": digest(current)}))
    return root, data, memory, splits, record


@pytest.mark.parametrize("changed", ["data", "code", "model", "memory"])
def test_frozen_snapshot_rejects_changes(tmp_path, monkeypatch, changed):
    root, data, memory, splits, record = frozen_fixture(tmp_path, monkeypatch)
    freeze.verify_freeze(record, data, memory, splits)
    if changed == "data":
        data.write_text("[1]")
    elif changed == "code":
        (root / "src/module.py").write_text("VERSION = 2\n")
    elif changed == "model":
        monkeypatch.setenv("DEEPSEEK_MODEL", "new-model")
    else:
        (memory / "test1.json").write_text("{}")
    with pytest.raises((ValueError, KeyError), match="changed|build_info|conversation_id"):
        freeze.verify_freeze(record, data, memory, splits)


def test_course_export_contains_only_required_answer_fields(tmp_path):
    from src.eval.export_answers import export_answers

    path = tmp_path / "internal.json"
    path.write_text(
        json.dumps(
            [
                {
                    "question_id": "q1",
                    "conversation_id": "conv-43",
                    "answer": "Boston",
                    "gold_answer": "Boston",
                    "gold_evidence": ["D1:1"],
                    "token_usage": 100,
                }
            ]
        )
    )
    output = tmp_path / "answers.json"
    assert export_answers(path, output, "conv-43") == 1
    assert json.loads(output.read_text()) == [{"question_id": "q1", "answer": "Boston"}]
