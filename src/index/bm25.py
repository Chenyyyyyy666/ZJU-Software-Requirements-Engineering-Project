"""BM25, vector and RRF retrieval over a validated conversation snapshot."""

import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import Field

from src.common.models import (
    ChannelScore,
    Contract,
    Memory,
    MemoryBundle,
    RetrievalHit,
    RetrievalRequest,
    RetrievalResult,
    SourceRef,
)
from src.index.embeddings import Encoder, MiniLMEncoder, VectorStore, build_vectors


def tokenize(text: str) -> list[str]:
    # LoCoMo is English; individual CJK characters provide a basic Chinese fallback.
    return re.findall(r"[a-z0-9]+|[\u3400-\u9fff]", text.casefold())


class IndexConfig(Contract):
    mode: Literal["bm25", "vector", "hybrid"] = "bm25"
    k1: float = Field(default=1.5, gt=0, allow_inf_nan=False)
    b: float = Field(default=0.75, ge=0, le=1, allow_inf_nan=False)
    fallback_window: int = Field(default=5, ge=0, le=20)
    fallback_slots: int = Field(default=2, ge=0, le=10)
    vector_cache_dir: Path = Path("indexes")
    model_cache_dir: Path = Path("cache/embedding_models")
    embedding_device: Literal["cpu", "cuda", "mps"] = "cpu"
    local_files_only: bool = False


@dataclass
class TurnWindowIndex:
    memories: list[Memory]
    counts: list[Counter]
    document_frequency: Counter
    average_length: float


@dataclass
class MemoryIndex:
    bundle: MemoryBundle
    config: IndexConfig
    counts: list[Counter]
    document_frequency: Counter
    average_length: float
    turn_windows: TurnWindowIndex | None
    vectors: VectorStore | None = None

    def validate_bundle(self, bundle: MemoryBundle) -> None:
        if (
            bundle.conversation_id != self.bundle.conversation_id
            or bundle.build_info.build_id != self.bundle.build_info.build_id
            or bundle.model_dump() != self.bundle.model_dump()
        ):
            raise RuntimeError("Index belongs to a different bundle; rebuild the index")


def _build_turn_windows(bundle: MemoryBundle, size: int) -> TurnWindowIndex | None:
    """Index contiguous raw-turn windows as an evidence-recovery safety net."""
    if size == 0:
        return None
    memories = []
    for start, first in enumerate(bundle.source_turns):
        window = [first]
        for turn in bundle.source_turns[start + 1 :]:
            if len(window) == size or turn.session_id != first.session_id:
                break
            window.append(turn)
        speakers = {turn.speaker for turn in window}
        memories.append(
            Memory(
                memory_id=f"raw-window-{first.turn_id}",
                conversation_id=bundle.conversation_id,
                content="\n".join(f"{turn.speaker}: {turn.text}" for turn in window),
                entity=next(iter(speakers)) if len(speakers) == 1 else None,
                attribute=None,
                value=None,
                time_expression=None,
                event_time=None,
                event_time_precision="unknown",
                sources=[
                    SourceRef(session_id=turn.session_id, turn_id=turn.turn_id) for turn in window
                ],
                session_time=window[-1].session_time,
            )
        )
    counts = [Counter(tokenize(memory.content)) for memory in memories]
    return TurnWindowIndex(
        memories=memories,
        counts=counts,
        document_frequency=Counter(term for document in counts for term in document),
        average_length=sum(sum(document.values()) for document in counts) / len(counts)
        if counts
        else 0,
    )


def _bm25_score(
    terms: set[str],
    counts: Counter,
    document_frequency: Counter,
    document_count: int,
    average_length: float,
    *,
    k1: float,
    b: float,
) -> float:
    score = 0.0
    for term in terms:
        frequency = counts[term]
        if not frequency:
            continue
        df = document_frequency[term]
        idf = math.log(1 + (document_count - df + 0.5) / (df + 0.5))
        denominator = frequency + k1 * (1 - b + b * sum(counts.values()) / average_length)
        score += idf * frequency * (k1 + 1) / denominator
    return score


def build_index(
    bundle: MemoryBundle, *, config: IndexConfig | None = None, encoder: Encoder | None = None
) -> MemoryIndex:
    snapshot = MemoryBundle.model_validate(bundle.model_dump())
    config = IndexConfig.model_validate((config or IndexConfig()).model_dump())
    counts = [
        Counter(tokenize(" ".join(filter(None, [m.content, m.entity, m.attribute, m.value]))))
        for m in snapshot.memories
    ]
    vectors = None
    if config.mode in {"vector", "hybrid"}:
        encoder = encoder or MiniLMEncoder(
            device=config.embedding_device,
            cache_folder=config.model_cache_dir,
            local_files_only=config.local_files_only,
        )
        vectors = build_vectors(snapshot, encoder, config.vector_cache_dir)
    return MemoryIndex(
        bundle=snapshot,
        config=config,
        counts=counts,
        document_frequency=Counter(term for doc in counts for term in doc),
        average_length=sum(sum(doc.values()) for doc in counts) / len(counts) if counts else 0,
        turn_windows=_build_turn_windows(snapshot, config.fallback_window),
        vectors=vectors,
    )


def _turn_window_candidates(
    turn_windows: TurnWindowIndex, terms: set[str], config: IndexConfig
) -> list[tuple[float, Memory]]:
    document_count = len(turn_windows.memories)
    ranked = []
    for memory, counts in zip(turn_windows.memories, turn_windows.counts, strict=True):
        score = _bm25_score(
            terms,
            counts,
            turn_windows.document_frequency,
            document_count,
            turn_windows.average_length,
            k1=config.k1,
            b=config.b,
        )
        if score > 0:
            ranked.append((score, memory))
    ranked.sort(key=lambda pair: (-pair[0], pair[1].memory_id))
    return ranked


def _source_signature(memory: Memory) -> tuple[tuple[int, str], ...]:
    return tuple(source.key for source in memory.sources)


def search_memories(index: MemoryIndex, request: RetrievalRequest) -> RetrievalResult:
    request = RetrievalRequest.model_validate(request.model_dump())
    if request.conversation_id != index.bundle.conversation_id:
        raise ValueError("Request and index conversation IDs differ")
    if request.mode != "bm25" and index.vectors is None:
        raise ValueError("Vector index not built; use IndexConfig(mode='vector' or 'hybrid')")
    terms = set(tokenize(request.query))
    ranked = []
    n = len(index.counts)
    positions = []
    for i, (memory, counts) in enumerate(zip(index.bundle.memories, index.counts, strict=True)):
        if request.entity is not None and memory.entity != request.entity:
            continue
        if request.attribute is not None and memory.attribute != request.attribute:
            continue
        positions.append(i)
        if request.mode == "vector":
            continue
        score = _bm25_score(
            terms,
            counts,
            index.document_frequency,
            n,
            index.average_length,
            k1=index.config.k1,
            b=index.config.b,
        )
        if score > 0:
            ranked.append((score, memory))
    ranked.sort(key=lambda pair: (-pair[0], pair[1].memory_id))
    channels = {}
    if request.mode in {"bm25", "hybrid"}:
        channels["bm25"] = ranked[: request.candidate_k]
    if request.mode in {"vector", "hybrid"}:
        vector_ranked = [
            (score, index.bundle.memories[i])
            for score, i in index.vectors.scores(request.query, positions)
        ]
        vector_ranked.sort(key=lambda pair: (-pair[0], pair[1].memory_id))
        channels["vector"] = vector_ranked[: request.candidate_k]
    scores = {}
    memories = {}
    for channel, candidates in channels.items():
        for rank, (score, memory) in enumerate(candidates, 1):
            memories[memory.memory_id] = memory
            scores.setdefault(memory.memory_id, {})[channel] = ChannelScore(rank=rank, score=score)
    fused = [
        (
            sum(1 / (60 + entry.rank) for entry in channel_scores.values())
            if request.mode == "hybrid"
            else next(iter(channel_scores.values())).score,
            memory_id,
        )
        for memory_id, channel_scores in scores.items()
    ]
    fused.sort(key=lambda pair: (-pair[0], pair[1]))

    selected: list[tuple[float, Memory, dict[str, ChannelScore]]] = []
    selected_memory_ids = set()
    selected_signatures = set()

    def select(memory: Memory, score: float, channel_scores: dict[str, ChannelScore]) -> bool:
        signature = _source_signature(memory)
        if memory.memory_id in selected_memory_ids or signature in selected_signatures:
            return False
        selected.append((score, memory, channel_scores))
        selected_memory_ids.add(memory.memory_id)
        selected_signatures.add(signature)
        return True

    raw_slots = 0
    if (
        request.mode == "hybrid"
        and request.entity is None
        and request.attribute is None
        and index.turn_windows is not None
        and index.config.fallback_window > 0
    ):
        raw_slots = min(index.config.fallback_slots, max(0, request.top_k - 1))
    preserved_semantic = min(len(fused), request.top_k - raw_slots)
    for score, memory_id in fused[:preserved_semantic]:
        select(memories[memory_id], score, scores[memory_id])

    raw_selected = 0
    raw_candidates = 0
    if raw_slots and index.turn_windows is not None:
        for rank, (score, memory) in enumerate(
            _turn_window_candidates(index.turn_windows, terms, index.config), 1
        ):
            raw_candidates += 1
            if raw_selected == raw_slots:
                break
            if select(
                memory,
                score,
                {"raw_window": ChannelScore(rank=rank, score=score)},
            ):
                raw_selected += 1

    for score, memory_id in fused[preserved_semantic:]:
        if len(selected) == request.top_k:
            break
        select(memories[memory_id], score, scores[memory_id])

    hits = [
        RetrievalHit(
            rank=rank,
            memory=memory.model_copy(deep=True),
            score=score,
            channel_scores=channel_scores,
        )
        for rank, (score, memory, channel_scores) in enumerate(selected[: request.top_k], 1)
    ]
    return RetrievalResult(
        conversation_id=request.conversation_id,
        build_id=index.bundle.build_info.build_id,
        query=request.query,
        mode=request.mode,
        hits=hits,
        trace={
            "top_k": request.top_k,
            "candidate_k": request.candidate_k,
            "entity": request.entity,
            "attribute": request.attribute,
            "channel_candidates": {name: len(rows) for name, rows in channels.items()},
            **(
                {
                    "embedding": index.vectors.metadata["encoder"],
                    "vector_cache": str(index.vectors.cache_path),
                    "fusion": "rrf-k60" if request.mode == "hybrid" else None,
                    "raw_window_fallback": (
                        {
                            "window": index.config.fallback_window,
                            "requested_slots": raw_slots,
                            "candidate_count": raw_candidates,
                            "selected_slots": raw_selected,
                            "preserved_semantic": preserved_semantic,
                        }
                        if request.mode == "hybrid"
                        else None
                    ),
                }
                if request.mode != "bm25"
                else {}
            ),
        },
    )
