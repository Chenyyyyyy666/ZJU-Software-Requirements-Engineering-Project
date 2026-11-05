"""Reject misleading final tables and keep report block updates scoped."""

import json

import pytest

from src.common.json_io import digest, write_json
from src.eval.finalize import (
    load_failed_attempts,
    load_relations,
    load_run,
    render_final_prose,
    render_main_table,
    report_blocks,
    update_block,
    update_completion_status,
    validate_split_comparability,
)


@pytest.fixture
def complete_run(tmp_path):
    manifest = {
        "runner_version": "final-eval-v1",
        "status": "complete",
        "dataset": "dev",
        "limit_per_conversation": None,
        "methods": ["full_text", "vector_rag", "ours"],
        "question_selection_counts": {"demo": {"selected_questions": 2}},
        "config_id": "test-config",
        "model": "test-model",
        "prompts": {"answer": {"version": "v1"}},
        "conversations": ["demo"],
        "top_k": 5,
        "candidate_k": 30,
        "ours_mode": "hybrid",
        "code_sha256": "code",
        "data_sha256": "data",
        "splits_sha256": "split",
        "question_policy": "nonempty reference only",
        "generation_temperature": 0,
        "thinking": "disabled",
    }
    write_json(manifest, tmp_path / "manifest.json")
    for method in manifest["methods"]:
        rows = [
            {
                "question_id": f"q-{index}",
                "conversation_id": "demo",
                "question": f"Question {index}?",
                "question_sha256": digest(f"Question {index}?"),
                "answer": "Boston",
                "gold_answer": "Boston",
                "gold_evidence": ["D1:1"],
                "method": method,
                "config_id": manifest["config_id"],
                "model": manifest["model"],
                "prompt_versions": manifest["prompts"],
                "token_usage": {"total_tokens": 10},
                "elapsed_seconds": 1,
                "retrieval_applicable": method != "full_text",
                "hits": [{"memory": {"sources": [{"turn_id": "D1:1"}]}}],
            }
            for index in (1, 2)
        ]
        write_json(
            {"config_id": manifest["config_id"], "answers": rows},
            tmp_path / method / "answers.json",
        )
    return tmp_path


def test_completed_run_recomputes_metrics_and_marks_full_text_recall_na(complete_run):
    run = load_run(complete_run, "dev")
    assert run["metrics"]["ours"]["qa_f1"] == 1.0
    assert run["metrics"]["full_text"]["recall@5"] is None
    table = render_main_table(run, title="Table")
    assert "Full Text | 1.0000 | 不适用 | 不适用" in table
    assert "本仓库实现" in table
    assert "token 有记录题数" in table


@pytest.mark.parametrize("field,value", [("status", "running"), ("limit_per_conversation", 1)])
def test_incomplete_or_smoke_runs_cannot_populate_final_tables(complete_run, field, value):
    path = complete_run / "manifest.json"
    manifest = json.loads(path.read_text())
    manifest[field] = value
    write_json(manifest, path)
    with pytest.raises(ValueError):
        load_run(complete_run, "dev")


@pytest.mark.parametrize(
    "mutation", ["different_question", "different_gold", "duplicate", "missing"]
)
def test_comparison_requires_same_questions_and_labels(complete_run, mutation):
    path = complete_run / "ours/answers.json"
    payload = json.loads(path.read_text())
    if mutation == "different_question":
        payload["answers"][0]["question_id"] = "different"
    elif mutation == "different_gold":
        payload["answers"][0]["gold_answer"] = "Changed gold"
    elif mutation == "duplicate":
        payload["answers"][1] = payload["answers"][0]
    else:
        payload["answers"].pop()
    write_json(payload, path)
    with pytest.raises(ValueError):
        load_run(complete_run, "dev")


def test_block_update_does_not_interpret_backslashes_or_change_other_prose():
    text = "Before\n<!-- BEGIN AUTO:table-dev -->old<!-- END AUTO:table-dev -->\nAfter"
    updated = update_block(text, "table-dev", r"C:\results\1")
    assert updated.startswith("Before") and updated.endswith("After")
    assert r"C:\results\1" in updated
    with pytest.raises(ValueError, match="exactly one"):
        update_block("Missing anchors", "table-dev", "new")


def test_dev_test_comparison_rejects_changed_model(complete_run):
    dev = load_run(complete_run, "dev")
    test = json.loads(json.dumps(dev))
    test["manifest"]["model"] = "other"
    with pytest.raises(ValueError, match="model"):
        validate_split_comparability(dev, test)


def test_relation_counts_cannot_disagree_with_matrix(tmp_path):
    labels = ["RETAIN", "UPDATE", "SUPERSEDE", "CONFLICT"]
    metrics = {
        "total_pairs": 10,
        "confusion_matrix": {gold: dict.fromkeys(labels, 0) for gold in labels},
        "failed_by_class": dict.fromkeys(labels, 0),
    }
    write_json(metrics, tmp_path / "metrics.json")
    write_json({"completed_at": "test", "summary": metrics}, tmp_path / "manifest.json")
    with pytest.raises(ValueError, match="counts are inconsistent"):
        load_relations(tmp_path)


def test_failed_attempt_unknown_tokens_are_reported_separately(tmp_path):
    path = tmp_path / "attempts.jsonl"
    rows = [
        {"config_id": "run", "method": "ours", "token_usage": {"total_tokens": 25}},
        {
            "config_id": "run",
            "method": "ours",
            "token_usage": {"total_tokens": None, "known_total_tokens": 7},
        },
    ]
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    summary = load_failed_attempts(path, "run")
    assert summary["ours"] == {"attempts": 2, "known_tokens": 32, "unknown_token_attempts": 1}


def test_dev_test_comparison_rejects_changed_endpoint(complete_run):
    dev = load_run(complete_run, "dev")
    test = json.loads(json.dumps(dev))
    dev["manifest"]["base_url"] = "https://provider.example/one"
    test["manifest"]["base_url"] = "https://provider.example/two"
    with pytest.raises(ValueError, match="base_url"):
        validate_split_comparability(dev, test)


def test_complete_snapshot_updates_factual_abstract_conclusion_and_status(complete_run):
    run = load_run(complete_run, "dev")
    labels = ["RETAIN", "UPDATE", "SUPERSEDE", "CONFLICT"]
    snapshot = {
        "generated_at_utc": "test",
        "dev": run,
        "test": run,
        "relations": {
            "metrics": {
                "total_pairs": 4,
                "successfully_evaluated": 4,
                "failed_or_missing": 0,
                "accuracy": 0.75,
                "macro_f1": 0.7,
                "confusion_matrix": {gold: dict.fromkeys(labels, 0) for gold in labels},
                "failed_by_class": dict.fromkeys(labels, 0),
                "per_class": {label: {"support": 1} for label in labels},
            },
            "manifest": {"config": {"model": "m", "prompt_version": "p", "gold_sha256": "g"}},
        },
    }
    blocks = report_blocks(snapshot)
    assert "abstract" in blocks and "conclusion" in blocks
    abstract, conclusion = render_final_prose(snapshot)
    assert "1.0000、1.0000、1.0000" in abstract
    assert "10.0" in abstract and "2 题" in abstract
    assert "Accuracy=0.7500" in conclusion
    assert "课程官方评分" in conclusion
    status = "> 版本：v1.0-result-pending\n> 当前状态：实验未产生\n"
    assert "实验已汇总，验收待核对" in update_completion_status(status, snapshot)
    assert update_completion_status(status, {"dev": run}) == status
    partial_blocks = report_blocks({"generated_at_utc": "test", "dev": run})
    assert "abstract" not in partial_blocks and "conclusion" not in partial_blocks
