"""Evaluate human-labelled memory pairs, or rescore recorded predictions offline.

Use ``python -m experiments.evaluate_relations --help`` from the repository root.
Gold labels, annotation rationale and evidence annotations are never passed to
``judge_pair``. Every pair is bound to one immutable MemoryBundle build_id.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from experiments.prepare_relation_labels import IDENTITY_FIELDS
from src.common.json_io import write_json as atomic_write_json
from src.common.models import Memory, MemoryBundle
from src.memory.store import load_memories
from src.reasoning.relation_judge import PROMPT_VERSION, SYSTEM_PROMPT, judge_pair

LABELS = ("RETAIN", "UPDATE", "SUPERSEDE", "CONFLICT")
PREDICTION_FIELDS = (
    *IDENTITY_FIELDS,
    "status",
    "predicted_relation",
    "model_reason",
    "error",
    "elapsed_seconds",
    "total_tokens",
    "model",
    "prompt_version",
)


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise ValueError(f"empty CSV: {path}")
        missing = set(IDENTITY_FIELDS) - set(reader.fieldnames)
        if missing:
            raise ValueError(f"CSV missing identity columns: {sorted(missing)}")
        return [{key: (value or "").strip() for key, value in row.items()} for row in reader]


def load_samples(path: Path) -> list[dict[str, str]]:
    rows = read_csv(path)
    if not rows:
        raise ValueError("gold CSV contains no pairs")
    seen = set()
    for row in rows:
        if any(not row.get(field) for field in IDENTITY_FIELDS):
            raise ValueError("all identity fields, including build_id, must be non-empty")
        if row["pair_id"] in seen:
            raise ValueError(f"duplicate pair_id: {row['pair_id']}")
        seen.add(row["pair_id"])
        if row.get("gold_relation") not in LABELS:
            raise ValueError(f"unlabelled/invalid gold_relation for {row['pair_id']}")
        if not row.get("evidence"):
            raise ValueError(f"missing human evidence references for {row['pair_id']}")
        if not row.get("annotator"):
            raise ValueError(f"missing annotator for {row['pair_id']}")
    return rows


def validate_pairs(
    samples: list[dict[str, str]], memory_dir: Path
) -> tuple[dict[str, tuple[Memory, Memory]], dict[str, str]]:
    bundles: dict[str, MemoryBundle] = {}
    inputs = {}
    pairs = {}
    for sample in samples:
        conversation = sample["conversation_id"]
        if conversation not in bundles:
            path = memory_dir / f"{conversation}.json"
            bundle = load_memories(path)
            if bundle.conversation_id != conversation:
                raise ValueError(f"MemoryBundle conversation mismatch: {conversation}")
            bundles[conversation] = bundle
            inputs[str(path)] = sha256_file(path)
        bundle = bundles[conversation]
        if bundle.build_info.build_id != sample["build_id"]:
            raise ValueError(f"build_id mismatch for {sample['pair_id']}")
        memories = {memory.memory_id: memory for memory in bundle.memories}
        try:
            a, b = memories[sample["memory_a_id"]], memories[sample["memory_b_id"]]
        except KeyError as exc:
            raise ValueError(f"missing memory ID for {sample['pair_id']}") from exc
        if a.memory_id == b.memory_id:
            raise ValueError(f"self-pair is invalid: {sample['pair_id']}")
        if (
            a.entity is None
            or a.attribute is None
            or (a.entity, a.attribute)
            != (
                b.entity,
                b.attribute,
            )
        ):
            raise ValueError(f"pair must share one non-empty entity/attribute: {sample['pair_id']}")
        order = {turn.key: i for i, turn in enumerate(bundle.source_turns)}
        if order[a.sources[-1].key] > order[b.sources[-1].key]:
            raise ValueError(f"A must appear no later than B: {sample['pair_id']}")
        allowed_evidence = {f"{ref.session_id}:{ref.turn_id}" for ref in [*a.sources, *b.sources]}
        evidence = {item.strip() for item in sample["evidence"].split(";") if item.strip()}
        if not evidence or not evidence <= allowed_evidence:
            raise ValueError(
                f"evidence must reference this pair's source keys: {sample['pair_id']}"
            )
        pairs[sample["pair_id"]] = a, b
    return pairs, inputs


def read_predictions(path: Path, samples: list[dict[str, str]]) -> dict[str, dict[str, Any]]:
    if path.suffix.lower() == ".csv":
        rows = read_csv(path)
    else:
        payload = json.loads(path.read_text(encoding="utf-8"))
        rows = payload.get("predictions", []) if isinstance(payload, dict) else payload
        if not isinstance(rows, list):
            raise ValueError("predictions JSON must be a list or contain predictions")
    lookup = {sample["pair_id"]: sample for sample in samples}
    predictions = {}
    for row in rows:
        pair_id = row.get("pair_id")
        if pair_id not in lookup:
            raise ValueError(f"prediction not in gold set: {pair_id}")
        if pair_id in predictions:
            raise ValueError(f"duplicate prediction: {pair_id}")
        for field in IDENTITY_FIELDS:
            if row.get(field) != lookup[pair_id][field]:
                raise ValueError(f"prediction {field} mismatch: {pair_id}")
        if (
            row.get("status", "success") == "success"
            and row.get("predicted_relation") not in LABELS
        ):
            raise ValueError(f"invalid predicted relation: {pair_id}")
        predictions[pair_id] = {**row, "status": row.get("status", "success")}
    return predictions


def summarize(samples: list[dict], predictions: dict[str, dict]) -> dict[str, Any]:
    cm = {gold: dict.fromkeys(LABELS, 0) for gold in LABELS}
    failed_by_class = dict.fromkeys(LABELS, 0)
    errors = []
    correct = 0
    for sample in samples:
        pair_id = sample["pair_id"]
        prediction = predictions.get(pair_id)
        gold = sample["gold_relation"]
        if prediction is None or prediction.get("status") != "success":
            failed_by_class[gold] += 1
            errors.append({**sample, "predicted_relation": "ERROR", "issue": "failed_or_missing"})
            continue
        predicted = prediction["predicted_relation"]
        cm[gold][predicted] += 1
        correct += predicted == gold
        if predicted != gold:
            errors.append({**sample, **prediction, "issue": "prediction_mismatch"})
    successful = sum(sum(row.values()) for row in cm.values())
    per_class = {}
    for label in LABELS:
        tp = cm[label][label]
        support = sum(cm[label].values()) + failed_by_class[label]
        predicted_count = sum(cm[gold][label] for gold in LABELS)
        precision = tp / predicted_count if predicted_count else 0.0
        recall = tp / support if support else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        per_class[label] = {
            "support": support,
            "successfully_evaluated": sum(cm[label].values()),
            "failed_or_missing": failed_by_class[label],
            "precision": precision,
            "recall": recall,
            "f1": f1,
        }
    return {
        "total_pairs": len(samples),
        "successfully_evaluated": successful,
        "failed_or_missing": len(samples) - successful,
        "correct": correct,
        "accuracy": correct / len(samples),
        "accuracy_successful_only": correct / successful if successful else None,
        "macro_f1": sum(item["f1"] for item in per_class.values()) / len(LABELS),
        "labels": list(LABELS),
        "confusion_matrix": cm,
        "failed_by_class": failed_by_class,
        "per_class": per_class,
        "denominator_policy": "accuracy and per-class recall include failed/missing predictions",
        "sampling_limit": (
            "targeted same-entity/attribute pairs; not an unbiased population estimate"
        ),
        "errors": errors,
    }


def write_json(path: Path, data: Any) -> None:
    atomic_write_json(data, path)


def write_outputs(output_dir: Path, samples: list[dict], predictions: dict[str, dict]) -> dict:
    ordered = [
        predictions[pair_id]
        for pair_id in (s["pair_id"] for s in samples)
        if pair_id in predictions
    ]
    write_json(output_dir / "predictions.json", ordered)
    with (output_dir / "relation_eval_results.csv").open(
        "w", encoding="utf-8-sig", newline=""
    ) as f:
        writer = csv.DictWriter(f, fieldnames=PREDICTION_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(ordered)
    summary = summarize(samples, predictions)
    errors = summary.pop("errors")
    write_json(output_dir / "metrics.json", summary)
    error_fields = (
        *IDENTITY_FIELDS,
        "gold_relation",
        "predicted_relation",
        "issue",
        "evidence",
        "model_reason",
    )
    with (output_dir / "relation-errors.csv").open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=error_fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(errors)
    parts = [
        "# State relation evaluation",
        "",
        f"- Pairs: {summary['total_pairs']}",
        f"- Success: {summary['successfully_evaluated']}; "
        f"failed/missing: {summary['failed_or_missing']}",
        f"- Accuracy (all pairs): {summary['accuracy']:.6f}",
        f"- Macro-F1 (four fixed classes): {summary['macro_f1']:.6f}",
        "- Rows = human gold, columns = prediction. API failures are reported separately.",
        "- Targeted candidate sampling; this result does not estimate all possible memory pairs.",
        "",
        "| Gold / Pred | " + " | ".join(LABELS) + " | Failed |",
        "| --- | " + " | ".join("---:" for _ in LABELS) + " | ---: |",
    ]
    for label in LABELS:
        parts.append(
            f"| {label} | "
            + " | ".join(str(summary["confusion_matrix"][label][p]) for p in LABELS)
            + f" | {summary['failed_by_class'][label]} |"
        )
    (output_dir / "summary.md").write_text("\n".join(parts) + "\n", encoding="utf-8")
    return summary


def live_prediction(sample: dict, pair: tuple[Memory, Memory], model: str) -> dict:
    # This function receives only pair identity plus the two model Memory objects.
    # Gold/evidence/annotator/rationale are intentionally not passed to judge_pair.
    from src.reasoning.telemetry import capture_usage

    identity = {field: sample[field] for field in IDENTITY_FIELDS}
    started = time.perf_counter()
    stats = None
    try:
        with capture_usage() as stats:
            relation = judge_pair(*pair)
        if relation.reason.startswith("judge failed:"):
            raise ValueError("invalid model response after retries")
        return {
            **identity,
            "status": "success",
            "predicted_relation": relation.relation_type,
            "model_reason": relation.reason,
            "elapsed_seconds": time.perf_counter() - started,
            "token_usage": stats.to_dict(),
            "total_tokens": stats.to_dict().get("total_tokens"),
            "model": model,
            "prompt_version": PROMPT_VERSION,
        }
    except Exception as exc:
        return {
            **identity,
            "status": "failed",
            "predicted_relation": "",
            "error": type(exc).__name__,
            "elapsed_seconds": time.perf_counter() - started,
            "token_usage": stats.to_dict() if stats is not None else None,
            "total_tokens": stats.to_dict().get("total_tokens") if stats is not None else None,
            "model": model,
            "prompt_version": PROMPT_VERSION,
        }


def main() -> None:
    load_dotenv(Path.cwd() / ".env")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--memory-dir", type=Path, required=True)
    parser.add_argument("--gold", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--predictions-input", type=Path, help="Offline: no model/API calls")
    parser.add_argument("--model", default=os.environ.get("DEEPSEEK_MODEL", "deepseek-flash"))
    parser.add_argument("--resume", action="store_true", help="Reuse successes; retry failed pairs")
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()
    samples = load_samples(args.gold)
    pairs, memory_hashes = validate_pairs(samples, args.memory_dir)
    if args.validate_only:
        print(f"Validated {len(samples)} human-labelled, build-bound pairs; no API calls")
        return
    config = {
        "gold_file": str(args.gold),
        "gold_sha256": sha256_file(args.gold),
        "evaluator_sha256": sha256_file(Path(__file__)),
        "judge_sha256": sha256_file(
            Path(__file__).resolve().parents[1] / "src/reasoning/relation_judge.py"
        ),
        "memory_sha256": memory_hashes,
        "model": args.model,
        "base_url": os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        "prompt_version": PROMPT_VERSION,
        "prompt_sha256": hashlib.sha256(SYSTEM_PROMPT.encode()).hexdigest(),
        "mode": "offline" if args.predictions_input else "live",
        "predictions_sha256": sha256_file(args.predictions_input)
        if args.predictions_input
        else None,
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = args.output_dir / "manifest.json"
    previous = {}
    if manifest_path.exists():
        previous = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not args.resume or previous.get("config") != config:
            parser.error(
                "output exists; use a new output directory or --resume with identical inputs"
            )
    started_at = previous.get("started_at", datetime.now(UTC).isoformat())
    write_json(manifest_path, {"config": config, "started_at": started_at})
    if args.predictions_input:
        predictions = read_predictions(args.predictions_input, samples)
    else:
        load_dotenv(Path.cwd() / ".env")
        os.environ["DEEPSEEK_MODEL"] = args.model
        checkpoint = args.output_dir / "predictions.json"
        predictions = (
            read_predictions(checkpoint, samples) if args.resume and checkpoint.exists() else {}
        )
        for sample in samples:
            pair_id = sample["pair_id"]
            if predictions.get(pair_id, {}).get("status") == "success":
                continue
            predictions[pair_id] = live_prediction(sample, pairs[pair_id], args.model)
            # Save each result before the next API call, so interrupted runs resume.
            write_outputs(args.output_dir, samples, predictions)
            print(f"{pair_id}: {predictions[pair_id]['status']}", flush=True)
    summary = write_outputs(args.output_dir, samples, predictions)
    write_json(
        manifest_path,
        {
            "config": config,
            "started_at": started_at,
            "completed_at": datetime.now(UTC).isoformat(),
            "summary": summary,
        },
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if summary["failed_or_missing"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
