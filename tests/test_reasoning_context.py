"""Consumer contract tests; model quality is verified separately with real inputs."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from src.common.models import RetrievalRequest
from src.index import build_index, search_memories
from src.index.context import retrieve_context
from src.memory.build import BuildConfig, build_memories
from src.memory.store import load_memories, save_memories

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/locomo_synthetic.json"


@pytest.fixture
def index():
    bundle = build_memories(FIXTURE, "demo-01", config=BuildConfig(mode="turns"))
    # These annotations are explicit test data, not claimed as LLM extraction.
    for memory in [bundle.memories[0], bundle.memories[3]]:
        memory.entity = "Alex"
        memory.attribute = "adoption_status"
    return build_index(bundle)


def test_history_preserves_top_k_and_has_all_original_evidence(index):
    request = RetrievalRequest(
        conversation_id="demo-01",
        query="interviews",
        top_k=1,
        entity="Alex",
        attribute="adoption_status",
    )
    original = search_memories(index, request).model_dump(mode="json")
    context = retrieve_context(index, request, history_limit=10)
    assert context["retrieval"] == original
    assert context["build_info"]["build_id"] == original["build_id"]
    assert context["build_info"]["method"] == "turn_baseline"
    history = context["related_history"]
    assert len(history["memories"]) == 1
    assert not history["truncated"]
    assert "researching" in history["memories"][0]["content"]
    assert {turn["turn_id"] for turn in context["source_turns"]} == {"D1:1", "D2:1"}
    assert "GOLD_" not in json.dumps(context)
    context["source_turns"][0]["text"] = "mutated by consumer"
    context["retrieval"]["hits"][0]["memory"]["content"] = "mutated by consumer"
    assert search_memories(index, request).model_dump(mode="json") == original


def test_opt_in_history_empty_candidates_and_truncation(index):
    request = RetrievalRequest(
        conversation_id="demo-01", query="xylophone", entity="Alex", attribute="adoption_status"
    )
    context = retrieve_context(index, request)
    assert context["related_history"] is None
    assert context["source_turns"] == []
    history = retrieve_context(index, request, history_limit=1)
    assert history["retrieval"]["hits"] == []
    assert history["related_history"]["truncated"]
    assert len(history["source_turns"]) == 1


@pytest.mark.parametrize("limit", [0, -1, True])
def test_invalid_history_limit(index, limit):
    with pytest.raises(ValueError, match="positive integer"):
        retrieve_context(
            index,
            RetrievalRequest(conversation_id="demo-01", query="adoption"),
            history_limit=limit,
        )


def test_history_requires_explicit_filters_and_conversation_isolation(index):
    with pytest.raises(ValueError, match="entity and attribute"):
        retrieve_context(
            index, RetrievalRequest(conversation_id="demo-01", query="adoption"), history_limit=2
        )
    with pytest.raises(ValueError, match="conversation"):
        retrieve_context(index, RetrievalRequest(conversation_id="another", query="adoption"))


def test_saved_bundle_to_consumer_cli(tmp_path):
    bundle = build_memories(FIXTURE, "demo-01", config=BuildConfig(mode="turns"))
    memory_path = tmp_path / "memory.json"
    output_path = tmp_path / "context.json"
    save_memories(bundle, memory_path)
    assert load_memories(memory_path) == bundle
    subprocess.run(
        [
            sys.executable,
            "-m",
            "src.index.context",
            "--memory",
            str(memory_path),
            "--query",
            "interviews",
            "--top-k",
            "1",
            "--output",
            str(output_path),
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    context = json.loads(output_path.read_text(encoding="utf-8"))
    assert context["source_turns"][0]["turn_id"] == "D2:1"
    assert context["retrieval"]["build_id"] == bundle.build_info.build_id
    assert context["related_history"] is None
