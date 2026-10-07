"""Search an exported bundle: python -m src.index.search --help."""

import argparse
import os
from pathlib import Path

from dotenv import load_dotenv

from src.common.json_io import write_json
from src.common.models import RetrievalRequest
from src.index.bm25 import IndexConfig, build_index, search_memories
from src.memory.store import load_memories


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--memory", type=Path, required=True)
    parser.add_argument("--query", required=True)
    parser.add_argument("--top-k", type=int, default=int(os.getenv("RETRIEVAL_TOP_K", "10")))
    parser.add_argument("--candidate-k", type=int, default=30)
    parser.add_argument("--mode", choices=["bm25", "vector", "hybrid"], default="bm25")
    parser.add_argument("--device", choices=["cpu", "cuda", "mps"], default="cpu")
    parser.add_argument("--local-files-only", action="store_true")
    parser.add_argument("--entity")
    parser.add_argument("--attribute")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    bundle = load_memories(args.memory)
    request = RetrievalRequest(
        conversation_id=bundle.conversation_id,
        query=args.query,
        top_k=args.top_k,
        candidate_k=args.candidate_k,
        entity=args.entity,
        attribute=args.attribute,
        mode=args.mode,
    )
    result = search_memories(
        build_index(
            bundle,
            config=IndexConfig(
                mode=args.mode,
                embedding_device=args.device,
                model_cache_dir=Path(os.getenv("CACHE_DIR", "cache")) / "embedding_models",
                local_files_only=args.local_files_only,
            ),
        ),
        request,
    )
    if args.output:
        write_json(result.model_dump(mode="json"), args.output)
        print(f"{len(result.hits)} hits saved to {args.output}")
    else:
        print(result.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
