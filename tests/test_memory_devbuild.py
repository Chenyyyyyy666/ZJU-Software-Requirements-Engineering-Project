import json

import httpx
import pytest

from src.memory.build import BuildConfig
from src.memory.devbuild import DEVELOPMENT_IDS, run
from src.memory.extract import DeepSeekExtractor
from src.memory.locomo import load_conversation
from tests.test_memory_pipeline import FIXTURE, fact_response


def test_cache_only_miss_never_calls_api(tmp_path, monkeypatch):
    def forbidden(*args):
        pytest.fail("Cache-only mode made a network request")

    monkeypatch.setattr(DeepSeekExtractor, "_post", forbidden)
    extractor = DeepSeekExtractor(
        model="mock", cache_dir=tmp_path, config_id="test", cache_only=True
    )
    with pytest.raises(RuntimeError, match="no API request"):
        extractor.extract(load_conversation(FIXTURE, "demo-01")[:1])


def test_usage_includes_rejected_response_and_cache_costs_nothing(tmp_path, monkeypatch):
    events, calls = [], []
    monkeypatch.setattr("src.memory.extract.time.sleep", lambda _: None)

    def post(self, body):
        calls.append(body)
        return httpx.Response(
            200,
            json={
                "model": "mock-returned-version",
                "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
                "choices": [
                    {
                        "finish_reason": "length" if len(calls) == 1 else "stop",
                        "message": {"content": json.dumps(fact_response())},
                    }
                ],
            },
        )

    monkeypatch.setattr(DeepSeekExtractor, "_post", post)
    extractor = DeepSeekExtractor(
        model="mock", cache_dir=tmp_path, config_id="test", api_key="fake", on_event=events.append
    )
    turns = load_conversation(FIXTURE, "demo-01")[:1]
    facts = extractor.extract(turns)
    extractor.cache_only = True
    assert extractor.extract(turns) == facts
    assert len(calls) == 2
    assert [e.get("status") for e in events] == ["failed", "accepted", None]
    assert sum((e.get("usage") or {}).get("total_tokens", 0) for e in events) == 60
    assert events[0]["returned_model"] == "mock-returned-version"
    assert events[-1]["kind"] == "cache_hit"


def test_four_builds_and_replay_are_offline_with_mock(tmp_path, monkeypatch):
    record = json.loads(FIXTURE.read_text(encoding="utf-8"))[0]
    data = tmp_path / "dev.json"
    data.write_text(
        json.dumps([{**record, "sample_id": cid} for cid in DEVELOPMENT_IDS]), encoding="utf-8"
    )
    calls = []

    def post(self, body):
        calls.append(body)
        turn = json.loads(body["messages"][1]["content"])[0]
        fact = fact_response(turn["turn_id"])["memories"][0]
        fact["sources"][0]["session_id"] = turn["session_id"]
        return httpx.Response(
            200,
            json={
                "model": "mock",
                "usage": {"total_tokens": 12},
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": json.dumps({"memories": [fact]})},
                    }
                ],
            },
        )

    monkeypatch.setenv("DEEPSEEK_API_KEY", "fake")
    monkeypatch.setattr(DeepSeekExtractor, "_post", post)
    output = tmp_path / "output"
    config = BuildConfig(model="mock", cache_dir=tmp_path / "cache")
    assert run(data, output, "public", config) == 0
    assert len(calls) == 8
    manifest = json.loads(next(output.glob("runs/*/manifest.json")).read_text(encoding="utf-8"))
    assert manifest["status"] == "builds_complete_review_pending"
    for item in manifest["conversations"]:
        assert item["cache_replay_identical"]
        assert item["reported_total_tokens"] == 24
        assert item["usage_missing_attempts"] == 0
        assert all(e["kind"] == "cache_hit" for e in item["replay_events"])
    for path in output.glob("runs/*/*-review.json"):
        review = json.loads(path.read_text(encoding="utf-8"))
        assert review["review_status"] == "pending"
        assert all(s["review_status"] == "pending" for s in review["samples"])


def test_preflight_has_no_network_or_bundles(tmp_path, monkeypatch):
    from src.memory import devbuild

    monkeypatch.setattr(devbuild, "DEVELOPMENT_IDS", ("demo-01",))

    def forbidden(*args):
        pytest.fail("Preflight made a network request")

    monkeypatch.setattr(DeepSeekExtractor, "_post", forbidden)
    assert run(FIXTURE, tmp_path, "public", BuildConfig(), preflight=True) == 0
    assert not (tmp_path / "memory").exists()
