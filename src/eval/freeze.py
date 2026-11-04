"""Freeze the data, source code and configuration before independent testing."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from dotenv import load_dotenv

from src.common.json_io import digest, write_json

ROOT = Path(__file__).resolve().parents[2]


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def snapshot(data: Path, memory_dir: Path, splits: Path) -> dict:
    split = json.loads(splits.read_text(encoding="utf-8-sig"))
    dev, test = split.get("dev", []), split.get("test", [])
    if len(test) != 2 or len(set(test)) != 2 or set(dev) & set(test):
        raise ValueError("test must contain exactly two conversations outside dev")
    memory_hashes = {}
    for conversation in [*dev, *test]:
        path = memory_dir / f"{conversation}.json"
        if not path.exists():
            raise ValueError(f"build MemoryBundle before freezing: {conversation}")
        bundle = json.loads(path.read_text(encoding="utf-8"))
        if bundle.get("conversation_id") != conversation:
            raise ValueError(f"wrong conversation_id: {path}")
        memory_hashes[conversation] = {
            "sha256": file_hash(path),
            "build_id": bundle["build_info"]["build_id"],
        }
    files = sorted(
        {
            *ROOT.joinpath("src").rglob("*.py"),
            *ROOT.joinpath("experiments").glob("*.py"),
            *ROOT.glob("requirements*.txt"),
            ROOT / "pyproject.toml",
        }
    )
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
    return {
        "python_version": platform.python_version(),
        "dependencies": dependencies,
        "data_sha256": file_hash(data),
        "splits_sha256": file_hash(splits),
        "dev": dev,
        "test": test,
        "memory_versions": memory_hashes,
        "code_files": {str(p.relative_to(ROOT)): file_hash(p) for p in files},
        "model": os.environ.get("DEEPSEEK_MODEL", "deepseek-flash"),
        "base_url": os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
    }


def verify_freeze(record: Path, data: Path, memory_dir: Path, splits: Path) -> dict:
    saved = json.loads(record.read_text(encoding="utf-8"))
    current = snapshot(data, memory_dir, splits)
    if digest(current) != saved.get("snapshot_sha256"):
        changed = [
            key for key, value in current.items() if saved.get("snapshot", {}).get(key) != value
        ]
        raise ValueError(f"frozen snapshot changed: {', '.join(changed)}; do not retune on test")
    return saved


def main() -> None:
    load_dotenv(ROOT / ".env")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--memory-dir", type=Path, required=True)
    parser.add_argument("--splits", type=Path, default=Path("experiments/splits.json"))
    parser.add_argument("--output", type=Path, default=Path("outputs/final/freeze.json"))
    parser.add_argument("--dev-dir", type=Path, required=True)
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--candidate-k", type=int, default=30)
    args = parser.parse_args()
    inputs = {args.data.resolve(), args.splits.resolve()}
    inputs.update(path.resolve() for path in args.memory_dir.glob("*.json"))
    if args.output.resolve() in inputs:
        raise ValueError("freeze output cannot overwrite input data, split or MemoryBundle")
    if args.output.exists():
        verify_freeze(args.output, args.data, args.memory_dir, args.splits)
        print(f"Existing freeze verified: {args.output}")
        return
    dev = json.loads((args.dev_dir / "manifest.json").read_text(encoding="utf-8"))
    from src.eval.finalize import load_run

    load_run(args.dev_dir, "dev")
    if dev.get("status") != "complete" or dev.get("limit_per_conversation") is not None:
        raise ValueError("complete the full development run before freezing")
    if set(dev.get("methods", [])) != {"ours", "full_text", "vector_rag"}:
        raise ValueError("complete all three development methods before freezing")
    current = snapshot(args.data, args.memory_dir, args.splits)
    if set(dev.get("conversations", [])) != set(current["dev"]):
        raise ValueError("development run does not cover the frozen dev split")
    if dev.get("data_sha256") != current["data_sha256"]:
        raise ValueError("development data version differs")
    if dev.get("splits_sha256") != current["splits_sha256"]:
        raise ValueError("data split changed after the development run")
    for conversation in current["dev"]:
        previous = dev.get("memory_versions", {}).get(conversation, {})
        now = current["memory_versions"][conversation]
        if any(previous.get(key) != now[key] for key in ("sha256", "build_id")):
            raise ValueError(f"development MemoryBundle changed: {conversation}")
    if dev.get("model") != current["model"]:
        raise ValueError("development model differs from freeze model")
    if dev.get("base_url") != current["base_url"]:
        raise ValueError("development API endpoint differs from freeze endpoint")
    if dev.get("dependencies") != current["dependencies"]:
        raise ValueError("installed dependencies changed after development")
    if dev.get("python_version") != current["python_version"]:
        raise ValueError("Python version changed after development")
    if dev.get("top_k") != args.top_k or dev.get("candidate_k") != args.candidate_k:
        raise ValueError("development retrieval settings differ")
    from experiments.run_final_eval import code_snapshot

    if dev.get("code_sha256") != digest(code_snapshot()):
        raise ValueError("source code changed after development run; rerun development first")
    policy = {
        "ours_mode": dev["ours_mode"],
        "top_k": args.top_k,
        "candidate_k": args.candidate_k,
        "question_policy": dev["question_policy"],
        "methods": dev["methods"],
        "generation_temperature": 0,
    }
    write_json(
        {
            "created_at_utc": datetime.now(UTC).isoformat(),
            "snapshot": current,
            "snapshot_sha256": digest(current),
            "experiment_policy": policy,
            "development_config_id": dev["config_id"],
            "development_manifest_sha256": file_hash(args.dev_dir / "manifest.json"),
            "python": platform.python_version(),
            "selection": "User authorized any two non-dev conversations; fixed conv-43/conv-44",
        },
        args.output,
    )
    print(f"Frozen: {args.output}. Keep code, data, prompts and parameters unchanged for test.")


if __name__ == "__main__":
    main()
