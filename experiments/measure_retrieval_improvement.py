"""Compare semantic hybrid retrieval with raw-turn-window fallback on dev data."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from src.common.json_io import write_json
from src.common.models import RetrievalRequest
from src.eval.metrics import recall_at_k
from src.index.bm25 import IndexConfig, build_index, search_memories
from src.index.embeddings import MiniLMEncoder
from src.memory.store import load_memories

DEFAULT_CONVERSATIONS = ("conv-26", "conv-30", "conv-41", "conv-42")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--memory-dir", type=Path, required=True)
    parser.add_argument("--splits", type=Path, default=Path("experiments/splits.json"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--conversations", nargs="*", default=list(DEFAULT_CONVERSATIONS))
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--candidate-k", type=int, default=30)
    parser.add_argument("--fallback-window", type=int, default=5)
    parser.add_argument("--fallback-slots", type=int, default=2)
    parser.add_argument("--device", choices=["cpu", "cuda", "mps"], default="cpu")
    parser.add_argument("--vector-cache-dir", type=Path, default=Path("cache/indexes"))
    return parser


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _summary(rows: list[dict[str, float | None]]) -> dict[str, Any]:
    values = [row for row in rows if row["recall@1"] is not None]
    count = len(values)
    return {
        "questions": count,
        "recall@1": sum(float(row["recall@1"]) for row in values) / count if count else None,
        "recall@5": sum(float(row["recall@5"]) for row in values) / count if count else None,
    }


def measure(args: argparse.Namespace) -> dict[str, Any]:
    if args.top_k <= 0 or args.candidate_k < args.top_k:
        raise ValueError("0 < --top-k <= --candidate-k is required")
    splits = _load(args.splits)
    if not set(args.conversations) <= set(splits.get("dev", [])):
        raise ValueError("all measured conversations must belong to the development split")
    payload = {item["sample_id"]: item for item in _load(args.data)}
    encoder = MiniLMEncoder(
        device=args.device,
        cache_folder=Path("cache/embedding_models"),
        local_files_only=True,
    )
    conversations = {}
    all_before = []
    all_after = []
    for conversation in args.conversations:
        bundle = load_memories(args.memory_dir / f"{conversation}.json")
        baseline = build_index(
            bundle,
            config=IndexConfig(
                mode="hybrid",
                fallback_window=0,
                embedding_device=args.device,
                vector_cache_dir=args.vector_cache_dir,
                local_files_only=True,
            ),
            encoder=encoder,
        )
        improved = build_index(
            bundle,
            config=IndexConfig(
                mode="hybrid",
                fallback_window=args.fallback_window,
                fallback_slots=args.fallback_slots,
                embedding_device=args.device,
                vector_cache_dir=args.vector_cache_dir,
                local_files_only=True,
            ),
            encoder=encoder,
        )
        rows = []
        for question in payload[conversation]["qa"]:
            evidence = question.get("evidence") or []
            if not question.get("answer") or not evidence:
                continue
            request = RetrievalRequest(
                conversation_id=conversation,
                query=question["question"],
                top_k=args.top_k,
                candidate_k=args.candidate_k,
                mode="hybrid",
            )
            before = search_memories(baseline, request)
            after = search_memories(improved, request)
            row = {
                "before_recall@1": recall_at_k(before.hits, evidence, 1),
                "before_recall@5": recall_at_k(before.hits, evidence, 5),
                "after_recall@1": recall_at_k(after.hits, evidence, 1),
                "after_recall@5": recall_at_k(after.hits, evidence, 5),
            }
            rows.append(row)
            all_before.append(
                {"recall@1": row["before_recall@1"], "recall@5": row["before_recall@5"]}
            )
            all_after.append({"recall@1": row["after_recall@1"], "recall@5": row["after_recall@5"]})
        before_summary = _summary(
            [
                {"recall@1": row["before_recall@1"], "recall@5": row["before_recall@5"]}
                for row in rows
            ]
        )
        after_summary = _summary(
            [{"recall@1": row["after_recall@1"], "recall@5": row["after_recall@5"]} for row in rows]
        )
        conversations[conversation] = {
            "before": before_summary,
            "after": after_summary,
            "delta_recall@1": after_summary["recall@1"] - before_summary["recall@1"],
            "delta_recall@5": after_summary["recall@5"] - before_summary["recall@5"],
        }
    before = _summary(all_before)
    after = _summary(all_after)
    return {
        "dataset": "dev",
        "conversations": list(args.conversations),
        "top_k": args.top_k,
        "candidate_k": args.candidate_k,
        "fallback_window": args.fallback_window,
        "fallback_slots": args.fallback_slots,
        "before": before,
        "after": after,
        "delta_recall@1": after["recall@1"] - before["recall@1"],
        "delta_recall@5": after["recall@5"] - before["recall@5"],
        "per_conversation": conversations,
    }


def main() -> None:
    args = build_parser().parse_args()
    result = measure(args)
    write_json(result, args.output)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
