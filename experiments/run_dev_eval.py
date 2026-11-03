"""Run the project's evaluation entry point for the four published dev dialogs.

The script deliberately runs only the team's method.  Full Text and Vector RAG
must come from their official runner and can later be merged with
``src.eval.aggregate``.  Gold answers and evidence remain in the answer rows
for scoring only; they are never passed to the memory or reasoning functions.

Example::

    python experiments/run_dev_eval.py \
      --questions locomo.json \
      --memory-dir outputs/memory \
      --mode hybrid \
      --output-dir outputs/eval/dev-hybrid
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from src.common.json_io import write_json
from src.eval.cli import _load_json, _questions_from_payload, _run_live
from src.eval.metrics import evaluate_rows

DEFAULT_CONVERSATIONS = ("conv-26", "conv-30", "conv-41", "conv-42")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--questions", type=Path, required=True)
    parser.add_argument("--memory-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--mode", choices=["bm25", "vector", "hybrid"], default="hybrid")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--candidate-k", type=int, default=30)
    parser.add_argument("--device", choices=["cpu", "cuda", "mps"], default="cpu")
    parser.add_argument("--local-files-only", action="store_true")
    parser.add_argument("--conversations", nargs="*", default=list(DEFAULT_CONVERSATIONS))
    return parser


def _write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json(payload, path)


def main() -> None:
    args = build_parser().parse_args()
    if args.top_k <= 0 or args.candidate_k < args.top_k:
        raise SystemExit("--top-k must be positive and <= --candidate-k")
    payload = _load_json(args.questions)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    all_rows: list[dict[str, Any]] = []
    for conversation in args.conversations:
        questions = _questions_from_payload(payload, conversation)
        memory_path = args.memory_dir / f"{conversation}.json"
        rows = _run_live(
            questions,
            conversation,
            memory_path,
            args.mode,
            args.top_k,
            args.candidate_k,
            args.device,
            args.local_files_only,
        )
        all_rows.extend(rows)
        _write(args.output_dir / f"{conversation}-answers.json", rows)
        _write(args.output_dir / f"{conversation}-metrics.json", evaluate_rows(rows))
        print(f"wrote {len(rows)} rows for {conversation}")
    _write(
        args.output_dir / "dev-answers.json",
        {
            "dataset": "dev",
            "method": f"{args.mode}+state",
            "conversations": list(args.conversations),
            "answers": all_rows,
        },
    )
    _write(args.output_dir / "dev-metrics.json", evaluate_rows(all_rows))
    print(f"wrote {len(all_rows)} dev rows to {args.output_dir}")


if __name__ == "__main__":
    main()
