"""Relation experiments must preserve identity, gold isolation and failure counts."""

import csv
import json
from pathlib import Path

import pytest

from experiments.evaluate_relations import (
    load_samples,
    read_predictions,
    summarize,
    validate_pairs,
    write_outputs,
)
from experiments.prepare_relation_labels import LABEL_FIELDS, prepare, write_candidates


@pytest.fixture
def candidate(tmp_path):
    project = Path(__file__).resolve().parents[1]
    bundle = json.loads((project / "examples/demo_dynamic_memory.json").read_text())
    (tmp_path / "demo-state-change.json").write_text(json.dumps(bundle), encoding="utf-8")
    rows, records = prepare(tmp_path, ["demo-state-change"], 10)
    assert len(rows) == 1
    return tmp_path, rows, records


def save_gold(path, rows):
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=LABEL_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def labelled(row):
    return {
        **row,
        "gold_relation": "UPDATE",
        "evidence": "1:S1:1;2:S2:1",
        "annotator": "Human annotator",
    }


def prediction(row, relation="UPDATE"):
    return {
        key: row[key]
        for key in ("pair_id", "conversation_id", "build_id", "memory_a_id", "memory_b_id")
    } | {"status": "success", "predicted_relation": relation}


def test_candidates_leave_labels_blank_and_include_original_evidence(candidate):
    memory_dir, rows, records = candidate
    assert rows[0]["gold_relation"] == ""
    assert [turn["text"] for turn in records[0]["original_turns"]] == [
        "I am preparing for my adoption agency interview.",
        "I passed the adoption agency interviews. I am one step closer to building a family.",
    ]
    output = memory_dir / "labels"
    write_candidates(rows, records, output)
    with pytest.raises(ValueError, match="unlabelled"):
        load_samples(output / "memory_pairs_gold.csv")


def test_labels_bind_to_real_build_and_original_source_keys(candidate):
    memory_dir, rows, _ = candidate
    row = labelled(rows[0])
    pairs, hashes = validate_pairs([row], memory_dir)
    assert len(pairs) == 1 and len(hashes) == 1
    with pytest.raises(ValueError, match="build_id mismatch"):
        validate_pairs([{**row, "build_id": "wrong-build"}], memory_dir)
    with pytest.raises(ValueError, match="source keys"):
        validate_pairs([{**row, "evidence": "9:NONEXISTENT"}], memory_dir)


def test_reversed_temporal_pair_is_rejected(candidate):
    memory_dir, rows, _ = candidate
    row = labelled(rows[0])
    row["memory_a_id"], row["memory_b_id"] = row["memory_b_id"], row["memory_a_id"]
    with pytest.raises(ValueError, match="A must appear"):
        validate_pairs([row], memory_dir)


def test_gold_requires_human_annotation_and_unique_ids(candidate):
    memory_dir, rows, _ = candidate
    gold_path = memory_dir / "gold.csv"
    row = labelled(rows[0])
    save_gold(gold_path, [row, row])
    with pytest.raises(ValueError, match="duplicate"):
        load_samples(gold_path)
    save_gold(gold_path, [{**row, "annotator": ""}])
    with pytest.raises(ValueError, match="annotator"):
        load_samples(gold_path)


def test_offline_predictions_reject_cross_build_and_duplicates(candidate):
    memory_dir, rows, _ = candidate
    row = labelled(rows[0])
    path = memory_dir / "predictions.json"
    path.write_text(json.dumps([{**prediction(row), "build_id": "other"}]))
    with pytest.raises(ValueError, match="build_id mismatch"):
        read_predictions(path, [row])
    path.write_text(json.dumps([prediction(row), prediction(row)]))
    with pytest.raises(ValueError, match="duplicate prediction"):
        read_predictions(path, [row])


def test_failed_prediction_counts_in_main_accuracy_and_class_recall(candidate):
    _, rows, _ = candidate
    a = labelled(rows[0])
    b = {**a, "pair_id": "second", "gold_relation": "CONFLICT"}
    predictions = {a["pair_id"]: prediction(a)}
    result = summarize([a, b], predictions)
    assert result["accuracy"] == 0.5
    assert result["accuracy_successful_only"] == 1.0
    assert result["failed_or_missing"] == 1
    assert result["per_class"]["CONFLICT"]["support"] == 1
    assert result["failed_by_class"]["CONFLICT"] == 1
    assert sum(sum(row.values()) for row in result["confusion_matrix"].values()) == 1


def test_offline_outputs_reproduce_csv_and_json(candidate):
    memory_dir, rows, _ = candidate
    row = labelled(rows[0])
    predictions = {row["pair_id"]: prediction(row, "RETAIN")}
    output_dir = memory_dir / "results"
    output_dir.mkdir()
    result = write_outputs(output_dir, [row], predictions)
    rescored = read_predictions(output_dir / "relation_eval_results.csv", [row])
    assert summarize([row], rescored)["confusion_matrix"] == result["confusion_matrix"]
    metrics = json.loads((output_dir / "metrics.json").read_text())
    assert metrics["confusion_matrix"]["UPDATE"]["RETAIN"] == 1
    assert metrics["accuracy"] == 0.0
    assert "prediction_mismatch" in (output_dir / "relation-errors.csv").read_text()


def test_live_predictor_passes_only_two_memories_without_gold(candidate, monkeypatch):
    from types import SimpleNamespace

    from experiments.evaluate_relations import live_prediction

    memory_dir, rows, _ = candidate
    row = labelled(rows[0])
    row["rationale"] = "SECRET_ANNOTATION_MUST_NOT_ENTER_MODEL"
    pairs, _ = validate_pairs([row], memory_dir)
    received = []

    def fake_judge(a, b):
        received.extend([a.model_dump(mode="json"), b.model_dump(mode="json")])
        return SimpleNamespace(relation_type="UPDATE", reason="synthetic test response")

    monkeypatch.setattr("experiments.evaluate_relations.judge_pair", fake_judge)
    prediction_row = live_prediction(row, pairs[row["pair_id"]], "test-model")
    assert prediction_row["status"] == "success"
    assert "SECRET_ANNOTATION" not in json.dumps(received)
    assert "gold_relation" not in prediction_row
    assert "annotator" not in prediction_row


def test_invalid_model_fallback_is_recorded_as_failure_not_conflict(candidate, monkeypatch):
    from types import SimpleNamespace

    from experiments.evaluate_relations import live_prediction

    memory_dir, rows, _ = candidate
    row = labelled(rows[0])
    pairs, _ = validate_pairs([row], memory_dir)
    monkeypatch.setattr(
        "experiments.evaluate_relations.judge_pair",
        lambda a, b: SimpleNamespace(relation_type="CONFLICT", reason="judge failed: invalid JSON"),
    )
    result = live_prediction(row, pairs[row["pair_id"]], "test-model")
    assert result["status"] == "failed"
    assert result["predicted_relation"] == ""


def test_resume_reuses_success_and_rejects_new_config(candidate, monkeypatch):
    from experiments.evaluate_relations import main

    memory_dir, rows, _ = candidate
    row = labelled(rows[0])
    gold_path = memory_dir / "gold.csv"
    save_gold(gold_path, [row])
    output_dir = memory_dir / "run"
    calls = []

    def fake_prediction(sample, pair, model):
        calls.append(sample["pair_id"])
        return prediction(sample)

    monkeypatch.setattr("experiments.evaluate_relations.live_prediction", fake_prediction)
    arguments = [
        "evaluate_relations",
        "--memory-dir",
        str(memory_dir),
        "--gold",
        str(gold_path),
        "--output-dir",
        str(output_dir),
        "--model",
        "test-model",
    ]
    monkeypatch.setattr("sys.argv", arguments)
    main()
    monkeypatch.setattr("sys.argv", arguments + ["--resume"])
    main()
    assert len(calls) == 1
    monkeypatch.setattr("sys.argv", arguments + ["--resume", "--model", "changed-model"])
    with pytest.raises(SystemExit):
        main()
