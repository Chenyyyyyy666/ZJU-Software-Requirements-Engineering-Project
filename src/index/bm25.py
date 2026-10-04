"""Small in-memory BM25 index, bound to a validated conversation snapshot."""

import math
import re
from collections import Counter
from dataclasses import dataclass
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


def tokenize(text: str) -> list[str]:
    # LoCoMo is English; individual CJK characters provide a basic Chinese fallback.
    return re.findall(r"[a-z0-9]+|[\u3400-\u9fff]", text.casefold())


class IndexConfig(Contract):
    mode: Literal["bm25"] = "bm25"
    k1: float = Field(default=1.5, gt=0, allow_inf_nan=False)
    b: float = Field(default=0.75, ge=0, le=1, allow_inf_nan=False)


@dataclass
class MemoryIndex:
    bundle: MemoryBundle
    config: IndexConfig
    counts: list[Counter]
    document_frequency: Counter
    average_length: float

    def validate_bundle(self, bundle: MemoryBundle) -> None:
        if (
            bundle.conversation_id != self.bundle.conversation_id
            or bundle.build_info.build_id != self.bundle.build_info.build_id
            or bundle.model_dump() != self.bundle.model_dump()
        ):
            raise RuntimeError("Index belongs to a different bundle; rebuild the index")


def build_index(bundle: MemoryBundle, *, config: IndexConfig | None = None) -> MemoryIndex:
    snapshot = MemoryBundle.model_validate(bundle.model_dump())
    counts = [
        Counter(tokenize(" ".join(filter(None, [m.content, m.entity, m.attribute, m.value]))))
        for m in snapshot.memories
    ]
    return MemoryIndex(
        bundle=snapshot,
        config=config or IndexConfig(),
        counts=counts,
        document_frequency=Counter(term for doc in counts for term in doc),
        average_length=sum(sum(doc.values()) for doc in counts) / len(counts) if counts else 0,
    )


def search_memories(index: MemoryIndex, request: RetrievalRequest) -> RetrievalResult:
    request = RetrievalRequest.model_validate(request.model_dump())
    if request.conversation_id != index.bundle.conversation_id:
        raise ValueError("Request and index conversation IDs differ")
    if request.mode != "bm25":
        raise ValueError("Only BM25 is implemented; vector/hybrid require a future backend")
    terms = set(tokenize(request.query))
    ranked = []
    n = len(index.counts)
    for memory, counts in zip(index.bundle.memories, index.counts, strict=True):
        if request.entity is not None and memory.entity != request.entity:
            continue
        if request.attribute is not None and memory.attribute != request.attribute:
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
    candidates = ranked[: request.candidate_k]
    hits = [
        RetrievalHit(
            rank=rank,
            memory=memory.model_copy(deep=True),
            score=score,
            channel_scores={"bm25": ChannelScore(rank=rank, score=score)},
        )
        for rank, (score, memory) in enumerate(candidates[: request.top_k], 1)
    ]
    return RetrievalResult(
        conversation_id=request.conversation_id,
        build_id=index.bundle.build_info.build_id,
        query=request.query,
        mode="bm25",
        hits=hits,
        trace={
            "top_k": request.top_k,
            "candidate_k": request.candidate_k,
            "entity": request.entity,
            "attribute": request.attribute,
            "channel_candidates": {"bm25": len(candidates)},
        },
    )
