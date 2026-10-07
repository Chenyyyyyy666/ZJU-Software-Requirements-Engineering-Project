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
    MemoryBundle,
    RetrievalHit,
    RetrievalRequest,
    RetrievalResult,
)
from src.index.embeddings import Encoder, MiniLMEncoder, VectorStore, build_vectors


def tokenize(text: str) -> list[str]:
    # LoCoMo is English; individual CJK characters provide a basic Chinese fallback.
    return re.findall(r"[a-z0-9]+|[\u3400-\u9fff]", text.casefold())


class IndexConfig(Contract):
    mode: Literal["bm25", "vector", "hybrid"] = "bm25"
    k1: float = Field(default=1.5, gt=0, allow_inf_nan=False)
    b: float = Field(default=0.75, ge=0, le=1, allow_inf_nan=False)
    vector_cache_dir: Path = Path("indexes")
    model_cache_dir: Path = Path("cache/embedding_models")
    embedding_device: Literal["cpu", "cuda", "mps"] = "cpu"
    local_files_only: bool = False


@dataclass
class MemoryIndex:
    bundle: MemoryBundle
    config: IndexConfig
    counts: list[Counter]
    document_frequency: Counter
    average_length: float
    vectors: VectorStore | None = None

    def validate_bundle(self, bundle: MemoryBundle) -> None:
        if (
            bundle.conversation_id != self.bundle.conversation_id
            or bundle.build_info.build_id != self.bundle.build_info.build_id
            or bundle.model_dump() != self.bundle.model_dump()
        ):
            raise RuntimeError("Index belongs to a different bundle; rebuild the index")


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
        vectors=vectors,
    )


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
        score = 0.0
        for term in terms:
            frequency = counts[term]
            if not frequency:
                continue
            df = index.document_frequency[term]
            idf = math.log(1 + (n - df + 0.5) / (df + 0.5))
            k1, b = index.config.k1, index.config.b
            denominator = frequency + k1 * (1 - b + b * sum(counts.values()) / index.average_length)
            score += idf * frequency * (k1 + 1) / denominator
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
    hits = [
        RetrievalHit(
            rank=rank,
            memory=memories[memory_id].model_copy(deep=True),
            score=score,
            channel_scores=scores[memory_id],
        )
        for rank, (score, memory_id) in enumerate(fused[: request.top_k], 1)
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
                }
                if request.mode != "bm25"
                else {}
            ),
        },
    )
