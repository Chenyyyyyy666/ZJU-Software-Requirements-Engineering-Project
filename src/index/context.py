"""Build a reasoning handoff with candidate evidence and explicitly requested history."""

import argparse
import os
from pathlib import Path

from dotenv import load_dotenv

from src.common.json_io import write_json
from src.common.models import RetrievalRequest
from src.index.bm25 import IndexConfig, MemoryIndex, build_index, search_memories
from src.memory.store import get_source_turns, list_related_memories, load_memories


def retrieve_context(
    index: MemoryIndex, request: RetrievalRequest, *, history_limit: int | None = None
) -> dict:
    """Return JSON-ready evidence; do not decide validity or generate an answer.

    History is opt-in and requires exact entity/attribute values supplied by C.
    Initial retrieval is preserved separately for E's Top-K measurements.
    All evidence is read from the same index snapshot as the returned memories.
    """
    request = RetrievalRequest.model_validate(request.model_dump())
    if history_limit is not None:
        if type(history_limit) is not int or history_limit <= 0:
            raise ValueError("history_limit must be a positive integer")
        if request.entity is None or request.attribute is None:
            raise ValueError("Explicit history requires both entity and attribute")
    result = search_memories(index, request)
    related = None
    if history_limit is not None:
        related = list_related_memories(
            index.bundle,
            entity=request.entity,
            attribute=request.attribute,
            exclude_ids=[hit.memory.memory_id for hit in result.hits],
            limit=history_limit,
        )
    memories = [hit.memory for hit in result.hits]
    if related is not None:
        memories.extend(related.memories)
    sources = get_source_turns(index.bundle, [ref for memory in memories for ref in memory.sources])
    return {
        "context_version": "0.1",
        "build_info": index.bundle.build_info.model_dump(mode="json"),
        "retrieval": result.model_dump(mode="json"),
        "related_history": related.model_dump(mode="json") if related is not None else None,
        "source_turns": [turn.model_dump(mode="json") for turn in sources],
    }


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--memory", type=Path, required=True)
    parser.add_argument("--query", required=True)
    parser.add_argument("--mode", choices=["bm25", "vector", "hybrid"], default="bm25")
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--candidate-k", type=int, default=30)
    parser.add_argument("--entity")
    parser.add_argument("--attribute")
    parser.add_argument("--history-limit", type=int)
    parser.add_argument("--device", choices=["cpu", "cuda", "mps"], default="cpu")
    parser.add_argument("--local-files-only", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    bundle = load_memories(args.memory)
    request = RetrievalRequest(
        conversation_id=bundle.conversation_id,
        query=args.query,
        mode=args.mode,
        top_k=args.top_k,
        candidate_k=args.candidate_k,
        entity=args.entity,
        attribute=args.attribute,
    )
    index = build_index(
        bundle,
        config=IndexConfig(
            mode=args.mode,
            embedding_device=args.device,
            model_cache_dir=Path(os.getenv("CACHE_DIR", "cache")) / "embedding_models",
            local_files_only=args.local_files_only,
        ),
    )
    context = retrieve_context(index, request, history_limit=args.history_limit)
    write_json(context, args.output)
    print(
        f"{len(context['retrieval']['hits'])} candidates, "
        f"{len(context['source_turns'])} source turns saved to {args.output}"
    )


if __name__ == "__main__":
    main()
