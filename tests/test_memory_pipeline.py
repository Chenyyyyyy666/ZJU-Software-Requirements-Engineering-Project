import json
import subprocess
import sys
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from src.common.models import MemoryBundle, RetrievalRequest, SourceRef
from src.index import build_index, search_memories
from src.memory.build import BuildConfig, build_memories, chunks
from src.memory.extract import DeepSeekExtractor
from src.memory.locomo import clean_text, load_conversation, normalize_session_time
from src.memory.store import (
    get_memory,
    get_source_turns,
    list_related_memories,
    load_memories,
    save_memories,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/locomo_synthetic.json"


@pytest.fixture
def bundle():
    return build_memories(FIXTURE, "demo-01", config=BuildConfig(mode="turns"))


def test_input_whitelist_and_chronology():
    turns = load_conversation(FIXTURE, "demo-01")
    exported = json.dumps([turn.model_dump() for turn in turns])
    for forbidden in ["GOLD_", "GENERATED_", "IMAGE_", "example.org"]:
        assert forbidden not in exported
    assert [turn.turn_id for turn in turns] == ["D1:1", "D1:2", "D1:3", "D2:1", "D2:2"]
    assert turns[0].session_time == "2023-05-08T13:56:00"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("12:00 am on 1 January, 2023", "2023-01-01T00:00:00"),
        ("12:00 pm on 1 January, 2023", "2023-01-01T12:00:00"),
        ("2023-05-08", "2023-05-08"),
        (None, None),
    ],
)
def test_session_dates(raw, expected):
    assert normalize_session_time(raw) == expected


def test_unknown_date_stays_unknown():
    with pytest.warns(UserWarning):
        assert normalize_session_time("31 February 2023") is None


def test_images_removed_but_normal_links_kept():
    text = "Read https://example.org/article ![caption](https://example.org/image) x.png"
    assert clean_text(text) == "Read https://example.org/article  x.png"
    assert clean_text("https://example.org/view?id=1", ["https://example.org/view?id=1"]) == ""


def test_bundle_roundtrip_and_stable_build(bundle, tmp_path):
    path = tmp_path / "nested" / "memory.json"
    save_memories(bundle, path)
    assert load_memories(path) == bundle
    again = build_memories(FIXTURE, "demo-01", config=BuildConfig(mode="turns"))
    assert again == bundle
    assert bundle.build_info.method == "turn_baseline"
    assert bundle.memories[0].entity is None


@pytest.mark.parametrize(
    "kind", ["duplicate", "missing_source", "cross_conversation", "time", "extra"]
)
def test_corrupt_bundles_rejected(bundle, kind):
    data = bundle.model_dump()
    if kind == "duplicate":
        data["memories"].append(data["memories"][0])
    elif kind == "missing_source":
        data["memories"][0]["sources"][0]["turn_id"] = "D99:99"
    elif kind == "cross_conversation":
        data["memories"][0]["conversation_id"] = "demo-02"
    elif kind == "time":
        data["memories"][0]["event_time"] = "2023-01-01"
    else:
        data["memories"][0]["answer"] = "must not enter memory"
    with pytest.raises(ValidationError):
        MemoryBundle.model_validate(data)


def test_unsupported_schema_rejected(bundle):
    data = bundle.model_dump()
    data["schema_version"] = "99"
    with pytest.raises(ValidationError):
        MemoryBundle.model_validate(data)


def test_evidence_lookup_and_missing_id(bundle):
    memory = get_memory(bundle, bundle.memories[-2].memory_id)
    sources = get_source_turns(bundle, memory.sources * 2)
    assert len(sources) == 1
    assert sources[0].turn_id == "D2:1"
    with pytest.raises(KeyError):
        get_memory(bundle, "missing")
    with pytest.raises(KeyError):
        get_source_turns(bundle, [SourceRef(session_id=9, turn_id="D9:9")])


def test_related_history_keeps_earlier_state(bundle):
    for memory in [bundle.memories[0], bundle.memories[3]]:
        memory.entity = "Alex"
        memory.attribute = "adoption_status"
    first = list_related_memories(
        bundle, entity="Alex", attribute="adoption_status", exclude_ids=[], limit=1
    )
    assert first.truncated
    assert "researching" in first.memories[0].content
    second = list_related_memories(
        bundle,
        entity="Alex",
        attribute="adoption_status",
        exclude_ids=[first.memories[0].memory_id],
        limit=1,
    )
    assert not second.truncated
    assert "passed" in second.memories[0].content


def test_retrieval_ranking_filters_and_isolation(bundle):
    index = build_index(bundle)
    request = RetrievalRequest(conversation_id="demo-01", query="interviews", top_k=1)
    result = search_memories(index, request)
    assert len(result.hits) == 1
    assert result.hits[0].memory.sources[0].turn_id == "D2:1"
    assert result.hits[0].rank == 1 and result.hits[0].score > 0
    assert result == search_memories(index, request)
    assert not search_memories(
        index, RetrievalRequest(conversation_id="demo-01", query="xylophone")
    ).hits
    with pytest.raises(ValueError, match="conversation"):
        search_memories(index, RetrievalRequest(conversation_id="demo-02", query="interviews"))
    with pytest.raises(ValueError, match="Vector index not built"):
        search_memories(
            index, RetrievalRequest(conversation_id="demo-01", query="adoption", mode="hybrid")
        )


def test_filter_before_top_k_and_snapshot(bundle):
    bundle.memories[0].entity = "Alex"
    index = build_index(bundle)
    result = search_memories(
        index, RetrievalRequest(conversation_id="demo-01", query="adoption", entity="Alex", top_k=1)
    )
    assert len(result.hits) == 1
    assert result.hits[0].memory.sources[0].turn_id == "D1:1"
    bundle.memories[0].content = "Changed without rebuilding"
    with pytest.raises(RuntimeError):
        index.validate_bundle(bundle)
    assert "researching" in result.hits[0].memory.content


@pytest.mark.parametrize(
    "overrides", [{"query": "  "}, {"top_k": 0}, {"top_k": True}, {"top_k": 31}]
)
def test_invalid_requests(overrides):
    fields = {"conversation_id": "demo-01", "query": "adoption", **overrides}
    with pytest.raises(ValidationError):
        RetrievalRequest(**fields)


def test_empty_conversation(tmp_path):
    path = tmp_path / "empty.json"
    path.write_text('[{"sample_id":"empty","conversation":{"session_1":[]}}]', encoding="utf-8")
    bundle = build_memories(path, "empty", config=BuildConfig(mode="turns"))
    assert not bundle.memories
    assert not search_memories(
        build_index(bundle), RetrievalRequest(conversation_id="empty", query="anything")
    ).hits
    with pytest.raises(KeyError):
        build_memories(path, "absent", config=BuildConfig(mode="turns"))


def test_chunk_limits_preserve_sessions():
    turns = load_conversation(FIXTURE, "demo-01")
    blocks = list(chunks(turns, BuildConfig(chunk_turns=2)))
    assert [len(block) for block in blocks] == [2, 1, 2]
    assert all(len({turn.session_id for turn in block}) == 1 for block in blocks)
    with pytest.raises(ValueError, match="exceeds"):
        list(chunks(turns, BuildConfig(chunk_chars=5)))


def fact_response(turn_id="D1:1"):
    return {
        "memories": [
            {
                "content": "Alex is researching adoption agencies.",
                "entity": "Alex",
                "attribute": "adoption_status",
                "value": "researching adoption agencies",
                "time_expression": None,
                "event_time": None,
                "event_time_precision": "unknown",
                "sources": [{"session_id": 1, "turn_id": turn_id}],
            }
        ]
    }


def response(payload):
    return httpx.Response(
        200,
        json={"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(payload)}}]},
    )


def test_deepseek_request_and_cache(tmp_path):
    calls = []

    def handler(request):
        body = json.loads(request.content)
        calls.append(body)
        assert body["thinking"] == {"type": "disabled"}
        assert body["response_format"] == {"type": "json_object"}
        assert "GOLD_" not in json.dumps(body)
        return response(fact_response())

    turns = load_conversation(FIXTURE, "demo-01")[:3]
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        extractor = DeepSeekExtractor(
            model="test-model", cache_dir=tmp_path, config_id="v1", client=client, api_key="fake"
        )
        first = extractor.extract(turns)
        assert extractor.extract(turns) == first
        assert len(calls) == 1
        extractor.model = "changed-model"
        extractor.extract(turns)
        assert len(calls) == 2


def test_bad_sources_retry_without_polluting_cache(tmp_path, monkeypatch):
    monkeypatch.setattr("src.memory.extract.time.sleep", lambda _: None)
    attempts = []

    def handler(request):
        attempts.append(1)
        return response(fact_response("D99:99"))

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        extractor = DeepSeekExtractor(
            model="test",
            cache_dir=tmp_path,
            config_id="v1",
            max_attempts=2,
            client=client,
            api_key="fake",
        )
        with pytest.raises(RuntimeError, match="after 2 attempts"):
            extractor.extract(load_conversation(FIXTURE, "demo-01")[:1])
    assert len(attempts) == 2
    assert not list(tmp_path.rglob("*.json"))


def test_auth_failure_is_not_retried_or_leaked(tmp_path):
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(401, text="secret-key-and-private-evidence")

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        extractor = DeepSeekExtractor(
            model="test", cache_dir=tmp_path, config_id="v1", client=client, api_key="fake"
        )
        with pytest.raises(PermissionError) as error:
            extractor.extract(load_conversation(FIXTURE, "demo-01")[:1])
    assert str(error.value) == "DeepSeek HTTP 401"
    assert len(calls) == 1


def test_build_with_mocked_deepseek(tmp_path, monkeypatch):
    # Exercise the builder and actual HTTP-response parser without making network calls.
    def post(self, body):
        turns = json.loads(body["messages"][1]["content"])
        facts = []
        for turn in turns:
            item = fact_response()["memories"][0]
            item["content"] = turn["text"]
            item["sources"] = [{"session_id": turn["session_id"], "turn_id": turn["turn_id"]}]
            facts.append(item)
        return response({"memories": facts})

    monkeypatch.setenv("DEEPSEEK_API_KEY", "fake")
    monkeypatch.setattr(DeepSeekExtractor, "_post", post)
    config = BuildConfig(cache_dir=tmp_path)
    bundle = build_memories(FIXTURE, "demo-01", config=config)
    assert bundle.build_info.method == "llm_extraction"
    assert len(bundle.memories) == 5
    assert build_memories(FIXTURE, "demo-01", config=config) == bundle


def test_cli_offline_end_to_end(tmp_path):
    memory_path = tmp_path / "memory.json"
    result_path = tmp_path / "result.json"
    subprocess.run(
        [
            sys.executable,
            "-m",
            "src.memory.build",
            "--data",
            str(FIXTURE),
            "--conversation",
            "demo-01",
            "--mode",
            "turns",
            "--output",
            str(memory_path),
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        [
            sys.executable,
            "-m",
            "src.index.search",
            "--memory",
            str(memory_path),
            "--query",
            "interviews",
            "--top-k",
            "1",
            "--output",
            str(result_path),
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    result = json.loads(result_path.read_text(encoding="utf-8"))
    assert result["hits"][0]["memory"]["sources"][0]["turn_id"] == "D2:1"


@pytest.mark.parametrize("failure", ["truncated", "malformed", "rate_limit"])
def test_transient_or_invalid_response_recovers(tmp_path, monkeypatch, failure):
    monkeypatch.setattr("src.memory.extract.time.sleep", lambda _: None)
    calls = []

    def handler(request):
        calls.append(1)
        if len(calls) == 1:
            if failure == "truncated":
                return httpx.Response(200, json={"choices": [{"finish_reason": "length"}]})
            if failure == "malformed":
                return httpx.Response(200, json={"choices": ["bad response"]})
            return httpx.Response(429)
        return response(fact_response())

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        extractor = DeepSeekExtractor(
            model="test", cache_dir=tmp_path, config_id="v1", client=client, api_key="fake"
        )
        assert len(extractor.extract(load_conversation(FIXTURE, "demo-01")[:1])) == 1
    assert len(calls) == 2


def test_corrupt_cache_does_not_make_paid_request(tmp_path):
    calls = []

    def handler(request):
        calls.append(1)
        return response(fact_response())

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        extractor = DeepSeekExtractor(
            model="test", cache_dir=tmp_path, config_id="v1", client=client, api_key="fake"
        )
        turns = load_conversation(FIXTURE, "demo-01")[:1]
        extractor.extract(turns)
        next(tmp_path.rglob("*.json")).write_text("broken JSON", encoding="utf-8")
        with pytest.raises(ValueError):
            extractor.extract(turns)
    assert len(calls) == 1


def test_failed_save_preserves_previous_file(bundle, tmp_path):
    path = tmp_path / "memory.json"
    save_memories(bundle, path)
    original = path.read_bytes()
    bundle.memories.append(bundle.memories[0])
    with pytest.raises(ValueError):
        save_memories(bundle, path)
    assert path.read_bytes() == original


def test_label_changes_do_not_change_build(tmp_path, bundle):
    records = json.loads(FIXTURE.read_text(encoding="utf-8"))
    records[0]["qa"] = [{"answer": "changed secret label"}]
    records[0]["event_summary"] = {"events": "changed secret summary"}
    path = tmp_path / "changed_labels.json"
    path.write_text(json.dumps(records), encoding="utf-8")
    assert build_memories(path, "demo-01", config=BuildConfig(mode="turns")) == bundle
