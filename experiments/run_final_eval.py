"""Run comparable, checkpointed local baselines and the team's method.

No model is called unless ``--execute`` is supplied. These Full Text and
Vector RAG baselines are project implementations, not the official LoCoMo
runners. Gold labels are added only after inference and are never passed to
the retrieval/reasoning functions. Restart an interrupted run with --resume.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import subprocess
import time
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from src.common.json_io import digest, write_json
from src.common.models import Memory, RetrievalRequest, SourceRef
from src.eval.cli import _question_id, _question_text, _questions_from_payload
from src.eval.metrics import evaluate_rows, valid_references
from src.memory.locomo import load_conversation
from src.memory.store import load_memories

PROJECT_ROOT = Path(__file__).resolve().parent.parent
METHODS = ("ours", "full_text", "vector_rag")
RUNNER_VERSION = "final-eval-v1"


def file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def code_snapshot() -> dict[str, str]:
    files = [*PROJECT_ROOT.joinpath("src").rglob("*.py"), Path(__file__).resolve()]
    return {str(path.relative_to(PROJECT_ROOT)): file_sha(path) for path in sorted(files)}


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _reference(row: dict[str, Any]) -> Any:
    return row.get("gold_answer", row.get("answer", row.get("reference_answer")))


def select_questions(
    payload: Any, conversation: str, limit: int | None, include_unscored: bool
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    raw = _questions_from_payload(payload, conversation)
    selected = []
    ids = set()
    skipped = 0
    for index, question in enumerate(raw):
        # Retain the source question number, even when unscored rows are skipped.
        identifier = _question_id(question, index)
        if identifier in ids:
            raise ValueError(f"duplicate question id in {conversation}: {identifier}")
        ids.add(identifier)
        text = _question_text(question)
        reference = _reference(question)
        if not include_unscored and not valid_references(reference):
            skipped += 1
            continue
        selected.append({**question, "question_id": identifier, "question": text})
    eligible = len(selected)
    if limit is not None:
        selected = selected[:limit]
    return selected, {
        "source_questions": len(raw),
        "excluded_without_reference": skipped,
        "eligible_questions": eligible,
        "selected_questions": len(selected),
    }


def build_plan(args: argparse.Namespace) -> tuple[dict[str, Any], dict[str, Any]]:
    inputs = [args.data, args.splits, *args.memory_dir.glob("*.json")]
    if any(path.resolve().is_relative_to(args.output_dir.resolve()) for path in inputs):
        raise ValueError("output directory cannot contain the input data, splits or memories")
    if args.limit is not None and args.limit <= 0:
        raise ValueError("--limit must be positive")
    if args.top_k <= 0 or args.candidate_k < args.top_k:
        raise ValueError("0 < --top-k <= --candidate-k is required")
    if len(set(args.methods)) != len(args.methods):
        raise ValueError("--methods must not contain duplicates")
    split = _load(args.splits)
    conversations = args.conversations or split.get(args.dataset, [])
    if not conversations or len(set(conversations)) != len(conversations):
        raise ValueError("select at least one distinct conversation")
    if args.dataset == "test" and set(conversations) != set(split.get("test", [])):
        raise ValueError("test conversations must exactly match the saved test split")
    if args.dataset == "dev" and not set(conversations) <= set(split.get("dev", [])):
        raise ValueError("development runs must stay within the saved development split")
    payload = _load(args.data)
    bundles, questions, counts = {}, {}, {}
    memory_hashes = {}
    for conversation in conversations:
        bundle_path = args.memory_dir / f"{conversation}.json"
        bundle = load_memories(bundle_path)
        if bundle.conversation_id != conversation:
            raise ValueError(f"bundle has wrong conversation id: {bundle_path}")
        original_turns = load_conversation(args.data, conversation)
        if bundle.source_turns != original_turns:
            raise ValueError(
                f"MemoryBundle sources differ from the evaluation data: {conversation}; "
                "use the matching data/build version"
            )
        bundles[conversation] = bundle
        questions[conversation], counts[conversation] = select_questions(
            payload, conversation, args.limit, args.include_unscored
        )
        if not questions[conversation]:
            raise ValueError(f"no eligible questions in {conversation}")
        memory_hashes[conversation] = {
            "sha256": file_sha(bundle_path),
            "build_id": bundle.build_info.build_id,
            "memories": len(bundle.memories),
            "source_turns": len(bundle.source_turns),
        }
    from src.reasoning import answer_generator, query_analyzer, relation_judge

    prompts = {
        module.__name__: {
            "version": module.PROMPT_VERSION,
            "sha256": digest(module.SYSTEM_PROMPT),
        }
        for module in (answer_generator, query_analyzer, relation_judge)
    }
    dependencies = {}
    for package in (
        "openai",
        "pydantic",
        "sentence-transformers",
        "transformers",
        "torch",
        "numpy",
    ):
        try:
            dependencies[package] = version(package)
        except PackageNotFoundError:
            dependencies[package] = None
    config = {
        "runner_version": RUNNER_VERSION,
        "base_url": os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "dependencies": dependencies,
        "dataset": args.dataset,
        "conversations": conversations,
        "methods": args.methods,
        "baseline_implementation": "local project baselines; not official LoCoMo runner",
        "question_policy": "all" if args.include_unscored else "nonempty reference only",
        "question_selection_counts": counts,
        "limit_per_conversation": args.limit,
        "ours_mode": args.ours_mode,
        "top_k": args.top_k,
        "candidate_k": args.candidate_k,
        "fallback_window": args.fallback_window,
        "fallback_slots": args.fallback_slots,
        "state_resolution": not args.disable_state_resolution,
        "device": args.device,
        "model": os.environ.get("DEEPSEEK_MODEL", "deepseek-flash"),
        "generation_temperature": 0,
        "thinking": "disabled",
        "prompts": prompts,
        "data_sha256": file_sha(args.data),
        "splits_sha256": file_sha(args.splits),
        "memory_versions": memory_hashes,
        "code_sha256": digest(code_snapshot()),
        "metric_implementation": "src.eval.metrics project token F1, not official scorer",
        "timing_policy": "per-question inference; excludes index/model initialization",
    }
    plan = {**config, "config_id": digest(config)}
    return plan, {"bundles": bundles, "questions": questions}


def _turn_memories(bundle: Any) -> list[Memory]:
    return [
        Memory(
            memory_id=f"raw-{turn.session_id}-{turn.turn_id}",
            conversation_id=turn.conversation_id,
            content=f"{turn.speaker}: {turn.text}",
            entity=turn.speaker,
            attribute=None,
            value=None,
            time_expression=None,
            event_time=None,
            event_time_precision="unknown",
            session_time=turn.session_time,
            sources=[SourceRef(session_id=turn.session_id, turn_id=turn.turn_id)],
        )
        for turn in bundle.source_turns
    ]


def run_question(
    *, method: str, question: str, question_id: str, bundle: Any, index: Any, args: Any
) -> dict[str, Any]:
    """Inference-only boundary. This function receives no QA labels."""
    from src.index.bm25 import search_memories
    from src.reasoning.answer_generator import generate_answer
    from src.reasoning.memory_selector import has_conflict, select_effective
    from src.reasoning.query_analyzer import analyze_query
    from src.reasoning.relation_judge import judge_relations
    from src.reasoning.telemetry import capture_usage

    started = time.perf_counter()
    with capture_usage() as usage:
        try:
            retrieval, relations, analysis = None, [], None
            if method == "full_text":
                effective = _turn_memories(bundle)
            else:
                mode = args.ours_mode if method == "ours" else "vector"
                retrieval = search_memories(
                    index,
                    RetrievalRequest(
                        conversation_id=bundle.conversation_id,
                        query=question,
                        top_k=args.top_k,
                        candidate_k=args.candidate_k,
                        mode=mode,
                    ),
                )
                memories = [hit.memory for hit in retrieval.hits]
                if method == "ours" and not args.disable_state_resolution:
                    analysis = analyze_query(
                        question, bundle.conversation_id, bundle.build_info.build_id
                    )
                    relations = judge_relations(memories)
                    effective = select_effective(
                        memories, relations, time_scope=analysis.time_scope
                    )
                else:
                    effective = memories
            result = generate_answer(
                question,
                effective,
                conflict=has_conflict(relations),
                build_id=bundle.build_info.build_id,
                question_id=question_id,
            )
        except BaseException as exc:
            exc.evaluation_telemetry = usage.to_dict()
            exc.evaluation_elapsed_seconds = time.perf_counter() - started
            raise
    return {
        **result.model_dump(mode="json"),
        "relations": [relation.model_dump(mode="json") for relation in relations],
        "query_analysis": analysis.model_dump(mode="json") if analysis else None,
        "retrieval": retrieval.model_dump(mode="json") if retrieval else None,
        "retrieval_applicable": method != "full_text",
        "token_usage": usage.to_dict(),
        "elapsed_seconds": time.perf_counter() - started,
    }


def read_checkpoint(path: Path, config_id: str) -> dict[tuple[str, str, str], dict[str, Any]]:
    rows = {}
    if not path.exists():
        return rows
    content = path.read_text(encoding="utf-8")
    lines = content.splitlines()
    for number, line in enumerate(lines, 1):
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            # A partial last write after interruption is ignored, never an inner corruption.
            if number == len(lines) and not content.endswith("\n"):
                break
            raise ValueError(f"corrupt checkpoint line {number}") from exc
        if row.get("config_id") != config_id:
            raise ValueError("checkpoint configuration differs; use a new output directory")
        key = (row["method"], row["conversation_id"], row["question_id"])
        if key in rows:
            raise ValueError(f"duplicate checkpoint row: {key}")
        rows[key] = row
    return rows


def _git_revision() -> str | None:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=PROJECT_ROOT, capture_output=True, text=True, check=False
    )
    return result.stdout.strip() if result.returncode == 0 else None


def _write_artifacts(args: Any, plan: dict[str, Any], rows: list[dict[str, Any]]) -> None:
    for method in args.methods:
        method_rows = [row for row in rows if row["method"] == method]
        directory = args.output_dir / method
        write_json(
            {
                "dataset": args.dataset,
                "method": method,
                "config_id": plan["config_id"],
                "answers": method_rows,
            },
            directory / "answers.json",
        )
        metrics = evaluate_rows(method_rows)
        if method == "full_text":
            metrics["recall@1"] = metrics["recall@5"] = None
        write_json(metrics, directory / "metrics.json")
        for conversation in plan["conversations"]:
            subset = [row for row in method_rows if row["conversation_id"] == conversation]
            write_json(subset, directory / f"{conversation}-answers.json")
            per_metrics = evaluate_rows(subset)
            if method == "full_text":
                per_metrics["recall@1"] = per_metrics["recall@5"] = None
            write_json(per_metrics, directory / f"{conversation}-metrics.json")


def run(args: argparse.Namespace) -> dict[str, Any]:
    load_dotenv(PROJECT_ROOT / ".env")
    plan, runtime = build_plan(args)
    print(json.dumps(plan, ensure_ascii=False, indent=2))
    if not args.execute:
        print("Preflight only. Add --execute to make paid model calls.")
        return plan
    if args.dataset == "test":
        if args.freeze_record is None:
            raise ValueError("test execution requires --freeze-record from the frozen experiment")
        # The freeze module validates the complete data/split/code/memory snapshot.
        from src.eval.freeze import verify_freeze

        frozen = verify_freeze(args.freeze_record, args.data, args.memory_dir, args.splits)
        if args.limit is not None:
            raise ValueError("test evaluation requires the full frozen question set")
        for name, value in frozen["experiment_policy"].items():
            if plan.get(name) != value:
                raise ValueError(f"test setting differs from freeze: {name}")
    if not os.environ.get("DEEPSEEK_API_KEY"):
        raise ValueError("set DEEPSEEK_API_KEY in the local .env before --execute")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    checkpoint = args.output_dir / "checkpoint.jsonl"
    manifest_path = args.output_dir / "manifest.json"
    if (checkpoint.exists() or manifest_path.exists()) and not args.resume:
        raise ValueError("output already contains a run; use --resume or a new directory")
    previous = {}
    if args.resume and manifest_path.exists():
        previous = _load(manifest_path)
        if previous.get("config_id") != plan["config_id"]:
            raise ValueError("run configuration differs; use a new output directory")
    completed = read_checkpoint(checkpoint, plan["config_id"]) if args.resume else {}
    expected = {
        (method, conversation, row["question_id"]): digest(row["question"])
        for conversation, questions in runtime["questions"].items()
        for row in questions
        for method in args.methods
    }
    for key, row in completed.items():
        if key not in expected or row.get("question_sha256") != expected[key]:
            raise ValueError("checkpoint contains an unexpected or changed question")
    # Rebuild a valid checkpoint prefix after an interrupted trailing JSON write.
    checkpoint.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in completed.values()),
        encoding="utf-8",
    )
    manifest = {
        **plan,
        "started_at_utc": previous.get("started_at_utc", datetime.now(UTC).isoformat()),
        "resumed_at_utc": datetime.now(UTC).isoformat() if previous else None,
        "git_commit": _git_revision(),
        "status": "running",
        "completed_rows": len(completed),
    }
    write_json(manifest, manifest_path)
    try:
        from src.index.bm25 import IndexConfig, build_index

        for conversation in plan["conversations"]:
            bundle = runtime["bundles"][conversation]
            indices = {}
            for method in args.methods:
                if method == "full_text":
                    continue
                mode = args.ours_mode if method == "ours" else "vector"
                if mode not in indices:
                    indices[mode] = build_index(
                        bundle,
                        config=IndexConfig(
                            mode=mode,
                            fallback_window=args.fallback_window,
                            fallback_slots=args.fallback_slots,
                            embedding_device=args.device,
                            model_cache_dir=args.model_cache_dir,
                            vector_cache_dir=args.output_dir / "vector-cache",
                            local_files_only=args.local_files_only,
                        ),
                    )
            for question in runtime["questions"][conversation]:
                for method in args.methods:
                    key = (method, conversation, question["question_id"])
                    if key in completed:
                        continue
                    mode = args.ours_mode if method == "ours" else "vector"
                    attempt_started = time.perf_counter()
                    try:
                        prediction = run_question(
                            method=method,
                            question=question["question"],
                            question_id=question["question_id"],
                            bundle=bundle,
                            index=indices.get(mode),
                            args=args,
                        )
                    except BaseException as exc:
                        attempt = {
                            "method": method,
                            "conversation_id": conversation,
                            "question_id": question["question_id"],
                            "config_id": plan["config_id"],
                            "status": "failed",
                            "error_type": type(exc).__name__,
                            "token_usage": getattr(exc, "evaluation_telemetry", None),
                            "elapsed_seconds": getattr(
                                exc,
                                "evaluation_elapsed_seconds",
                                time.perf_counter() - attempt_started,
                            ),
                            "recorded_at_utc": datetime.now(UTC).isoformat(),
                        }
                        with (args.output_dir / "attempts.jsonl").open(
                            "a", encoding="utf-8"
                        ) as handle:
                            handle.write(json.dumps(attempt, ensure_ascii=False) + "\n")
                            handle.flush()
                            os.fsync(handle.fileno())
                        manifest["failure_accounting"] = (
                            "attempts.jsonl includes failed question attempts; "
                            "unknown tokens remain null"
                        )
                        raise
                    row = {
                        **prediction,
                        "question_id": question["question_id"],
                        "conversation_id": conversation,
                        "question": question["question"],
                        "method": method,
                        "config_id": plan["config_id"],
                        "question_sha256": digest(question["question"]),
                        "gold_answer": _reference(question),
                        "gold_evidence": question.get("gold_evidence", question.get("evidence")),
                        "category": question.get("category"),
                        "model": plan["model"],
                        "prompt_versions": plan["prompts"],
                    }
                    with checkpoint.open("a", encoding="utf-8") as handle:
                        handle.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")
                        handle.flush()
                        os.fsync(handle.fileno())
                    completed[key] = row
                    manifest["completed_rows"] = len(completed)
                    write_json(manifest, manifest_path)
                    print(f"saved {method} {conversation} {question['question_id']}", flush=True)
            _write_artifacts(args, plan, list(completed.values()))
    except BaseException as exc:
        manifest["status"] = "interrupted" if isinstance(exc, KeyboardInterrupt) else "failed"
        manifest["failure_type"] = type(exc).__name__
        # Avoid logging arbitrary service error strings, response bodies or credentials.
        write_json(manifest, manifest_path)
        _write_artifacts(args, plan, list(completed.values()))
        raise
    manifest["status"] = "complete"
    manifest["finished_at_utc"] = datetime.now(UTC).isoformat()
    write_json(manifest, manifest_path)
    return manifest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--memory-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--dataset", choices=["dev", "test"], default="dev")
    parser.add_argument("--splits", type=Path, default=Path("experiments/splits.json"))
    parser.add_argument("--conversations", nargs="+")
    parser.add_argument("--methods", nargs="+", choices=METHODS, default=list(METHODS))
    parser.add_argument("--ours-mode", choices=["bm25", "vector", "hybrid"], default="hybrid")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--candidate-k", type=int, default=30)
    parser.add_argument("--fallback-window", type=int, default=5)
    parser.add_argument("--fallback-slots", type=int, default=2)
    parser.add_argument(
        "--disable-state-resolution",
        action="store_true",
        help="retrieval+answer ablation; skip query analysis and relation judging",
    )
    parser.add_argument("--limit", type=int, help="smoke-test questions per conversation")
    parser.add_argument("--include-unscored", action="store_true")
    parser.add_argument("--device", choices=["cpu", "cuda", "mps"], default="cpu")
    parser.add_argument("--model-cache-dir", type=Path, default=Path("cache/embedding_models"))
    parser.add_argument("--local-files-only", action="store_true")
    parser.add_argument("--freeze-record", type=Path)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--resume", action="store_true")
    return parser


if __name__ == "__main__":
    run(build_parser().parse_args())
