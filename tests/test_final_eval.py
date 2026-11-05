"""Final-run safety: label isolation, identical questions and restart behavior."""

import json
from pathlib import Path

import pytest

from experiments import run_final_eval as runner
from src.memory.build import BuildConfig, build_memories
from src.memory.store import save_memories

FIXTURE = Path(__file__).parent / "fixtures" / "locomo_synthetic.json"


@pytest.fixture
def run_args(tmp_path):
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    data[0]["qa"] = [
        {"question": "Where is Alex?", "answer": "Boston", "evidence": ["D1:1"]},
        {"question": "What happened?", "answer": "Interview", "evidence": ["D2:1"]},
    ]
    data_path = tmp_path / "data.json"
    data_path.write_text(json.dumps(data), encoding="utf-8")
    memory_dir = tmp_path / "memory"
    save_memories(
        build_memories(data_path, "demo-01", config=BuildConfig(mode="turns")),
        memory_dir / "demo-01.json",
    )
    split_path = tmp_path / "splits.json"
    split_path.write_text('{"dev":["demo-01"],"test":[]}', encoding="utf-8")
    return runner.build_parser().parse_args(
        [
            "--data",
            str(data_path),
            "--memory-dir",
            str(memory_dir),
            "--output-dir",
            str(tmp_path / "result"),
            "--splits",
            str(split_path),
            "--methods",
            "full_text",
        ]
    )


def test_selection_excludes_unscored_but_keeps_original_question_ids():
    questions, counts = runner.select_questions(
        [
            {"question": "Unscored?"},
            {"question": "Scored?", "answer": "gold must stay offline"},
        ],
        "demo-01",
        None,
        False,
    )
    assert [row["question_id"] for row in questions] == ["q-2"]
    assert counts["excluded_without_reference"] == 1


def test_preflight_does_not_invoke_models_or_create_output(run_args, monkeypatch):
    monkeypatch.setattr(runner, "run_question", lambda **kwargs: pytest.fail("model was invoked"))
    plan = runner.run(run_args)
    assert plan["question_selection_counts"]["demo-01"]["selected_questions"] == 2
    assert not run_args.output_dir.exists()


def test_preflight_rejects_mismatched_bundle_sources(run_args):
    data = json.loads(run_args.data.read_text(encoding="utf-8"))
    data[0]["conversation"]["session_1"][0]["text"] = "Another data version"
    run_args.data.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="sources differ"):
        runner.build_plan(run_args)


def test_checkpoint_resume_does_not_rerun_completed_questions(run_args, monkeypatch):
    calls = []

    def first_attempt(**kwargs):
        # Inference must receive only the current question; no gold/evidence arguments.
        assert set(kwargs) == {"method", "question", "question_id", "bundle", "index", "args"}
        calls.append(kwargs["question_id"])
        if kwargs["question_id"] == "q-2":
            raise RuntimeError("simulated service interruption")
        return {"answer": "Boston", "retrieval_applicable": False, "token_usage": 10}

    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-only-no-network")
    monkeypatch.setattr(runner, "run_question", first_attempt)
    run_args.execute = True
    with pytest.raises(RuntimeError, match="simulated"):
        runner.run(run_args)
    assert calls == ["q-1", "q-2"]
    calls.clear()

    def resumed(**kwargs):
        calls.append(kwargs["question_id"])
        return {"answer": "Interview", "retrieval_applicable": False, "token_usage": 20}

    monkeypatch.setattr(runner, "run_question", resumed)
    run_args.resume = True
    manifest = runner.run(run_args)
    assert calls == ["q-2"]
    assert manifest["status"] == "complete"
    answers = json.loads((run_args.output_dir / "full_text/answers.json").read_text())
    assert len(answers["answers"]) == 2
    assert answers["answers"][0]["gold_answer"] == "Boston"
    metrics = json.loads((run_args.output_dir / "full_text/metrics.json").read_text())
    assert metrics["recall@5"] is None
    assert metrics["qa_f1"] == 1.0


def test_resume_rejects_different_configuration(run_args, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-only-no-network")
    monkeypatch.setattr(runner, "run_question", lambda **kwargs: {"answer": "x"})
    run_args.execute = True
    runner.run(run_args)
    run_args.resume = True
    monkeypatch.setenv("DEEPSEEK_MODEL", "different-model")
    with pytest.raises(ValueError, match="configuration differs"):
        runner.run(run_args)


def test_checkpoint_recovers_only_unterminated_trailing_fragment(tmp_path):
    path = tmp_path / "checkpoint.jsonl"
    row = {"config_id": "abc", "method": "ours", "conversation_id": "d", "question_id": "q"}
    path.write_text(json.dumps(row) + '\n{"unfinished":', encoding="utf-8")
    assert len(runner.read_checkpoint(path, "abc")) == 1
    path.write_text(json.dumps(row) + '\n{"unfinished":\n', encoding="utf-8")
    with pytest.raises(ValueError, match="corrupt checkpoint"):
        runner.read_checkpoint(path, "abc")


def test_test_set_preflight_rejects_conversations_outside_frozen_split(run_args):
    run_args.dataset = "test"
    run_args.conversations = ["demo-01"]
    with pytest.raises(ValueError, match="exactly match"):
        runner.build_plan(run_args)
