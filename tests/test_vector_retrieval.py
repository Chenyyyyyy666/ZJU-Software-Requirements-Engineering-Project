"""Offline retrieval invariants; test encoders never download a model."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.common.models import RetrievalRequest
from src.index import IndexConfig, build_index, search_memories
from src.index.embeddings import MiniLMEncoder
from src.memory.build import BuildConfig, build_memories


class ToyEncoder:
    def __init__(self):
        self.calls = []
        self.revision = "test-v1"

    @property
    def metadata(self):
        return {"model": "test-only", "revision": self.revision, "dimension": 2}

    def encode(self, texts):
        self.calls.append(texts)
        return [
            [1.0, 0.0] if "passed" in text or "approval" in text else [0.0, 1.0] for text in texts
        ]


@pytest.fixture
def bundle():
    return build_memories(
        Path(__file__).parent / "fixtures/locomo_synthetic.json",
        "demo-01",
        config=BuildConfig(mode="turns"),
    )


def setup_index(bundle, tmp_path, encoder=None):
    encoder = encoder or ToyEncoder()
    return build_index(
        bundle, config=IndexConfig(mode="hybrid", vector_cache_dir=tmp_path), encoder=encoder
    ), encoder


def request(**overrides):
    return RetrievalRequest(
        **{"conversation_id": "demo-01", "query": "approval", "mode": "vector", **overrides}
    )


def test_vector_can_recall_without_word_overlap(bundle, tmp_path):
    index, _ = setup_index(bundle, tmp_path)
    result = search_memories(index, request(top_k=1))
    assert result.hits[0].memory.sources[0].turn_id == "D2:1"
    assert result.hits[0].score == pytest.approx(1)
    assert result.mode == "vector"
    assert result.trace["embedding"]["model"] == "test-only"
    assert not search_memories(index, request(mode="bm25")).hits


def test_rrf_union_deduplicates_and_preserves_channel_ranks(bundle, tmp_path):
    index, _ = setup_index(bundle, tmp_path)
    result = search_memories(index, request(query="interviews", mode="hybrid", top_k=5))
    assert len({hit.memory.memory_id for hit in result.hits}) == len(result.hits)
    assert result.hits[0].memory.sources[0].turn_id == "D2:1"
    for hit in result.hits:
        assert hit.score == pytest.approx(
            sum(1 / (60 + score.rank) for score in hit.channel_scores.values())
        )
    assert set(result.hits[0].channel_scores) == {"bm25", "vector"}
    assert result.trace["fusion"] == "rrf-k60"


def test_filter_before_vector_topk_and_no_query_when_empty(bundle, tmp_path):
    bundle.memories[0].entity = "Alex"
    bundle.memories[0].attribute = "adoption_status"
    index, encoder = setup_index(bundle, tmp_path)
    result = search_memories(
        index, request(entity="Alex", attribute="adoption_status", top_k=1, candidate_k=1)
    )
    assert result.hits[0].memory.sources[0].turn_id == "D1:1"
    calls = len(encoder.calls)
    assert not search_memories(index, request(entity="absent")).hits
    assert len(encoder.calls) == calls
    with pytest.raises(ValueError, match="conversation"):
        search_memories(index, request(conversation_id="demo-02"))


def test_persistent_cache_and_invalidation(bundle, tmp_path):
    index, encoder = setup_index(bundle, tmp_path)
    assert len(encoder.calls) == 1
    loaded, _ = setup_index(bundle, tmp_path, encoder)
    assert loaded.vectors.cache_path == index.vectors.cache_path
    assert len(encoder.calls) == 1
    bundle.memories[0].content = "Changed content even without a new build ID"
    changed, _ = setup_index(bundle, tmp_path, encoder)
    assert changed.vectors.cache_path != index.vectors.cache_path
    assert len(encoder.calls) == 2
    encoder.revision = "test-v2"
    revised, _ = setup_index(bundle, tmp_path, encoder)
    assert revised.vectors.cache_path != changed.vectors.cache_path
    assert len(encoder.calls) == 3
    with pytest.raises(RuntimeError, match="Encoder changed"):
        search_memories(changed, request())


def test_corrupt_cache_rejected_without_reencoding(bundle, tmp_path):
    index, encoder = setup_index(bundle, tmp_path)
    path = index.vectors.cache_path
    data = json.loads(path.read_text(encoding="utf-8"))
    data["vectors"][0] = [10, 20]
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="Invalid vector cache"):
        setup_index(bundle, tmp_path, encoder)
    assert len(encoder.calls) == 1


@pytest.mark.parametrize("row", [[0.0, 0.0], [float("nan"), 0.0], [1.0], [True, 0]])
def test_invalid_vectors_never_persist(bundle, tmp_path, row):
    encoder = ToyEncoder()
    encoder.encode = lambda texts: [row for _ in texts]
    with pytest.raises(ValueError):
        setup_index(bundle, tmp_path, encoder)
    assert not list(tmp_path.glob("*.json"))


def test_empty_bundle_and_stable_ties(bundle, tmp_path):
    index, _ = setup_index(bundle, tmp_path)
    result = search_memories(index, request(query="unmatched"))
    tied = [hit.memory.memory_id for hit in result.hits if hit.score == 1]
    assert tied == sorted(tied)
    bundle.memories = []
    empty, _ = setup_index(bundle, tmp_path)
    assert not search_memories(empty, request(mode="hybrid")).hits


def test_vector_failure_does_not_silently_fall_back(bundle, tmp_path):
    index, encoder = setup_index(bundle, tmp_path)

    def fail(texts):
        raise RuntimeError("encoder unavailable")

    encoder.encode = fail
    with pytest.raises(RuntimeError, match="encoder unavailable"):
        search_memories(index, request(mode="hybrid"))


def test_minilm_rejects_truncation_before_encoding():
    encoder = MiniLMEncoder()
    encoder._model = SimpleNamespace(
        tokenizer=lambda *args, **kwargs: {"input_ids": [[1] * 257]},
        max_seq_length=256,
    )
    with pytest.raises(ValueError, match="exceeds 256"):
        encoder.encode(["too long"])
