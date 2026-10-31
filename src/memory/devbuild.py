"""Build the four development conversations with durable provenance and cache checks."""

import argparse
import hashlib
import os
import platform
import sys
import time
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from uuid import uuid4

from dotenv import load_dotenv

from src.common.json_io import write_json
from src.memory.build import BuildConfig, build_memories, chunks
from src.memory.extract import PROMPT, PROMPT_VERSION
from src.memory.locomo import load_conversation
from src.memory.store import load_memories, save_memories

DEVELOPMENT_IDS = ("conv-26", "conv-30", "conv-41", "conv-42")


def review_samples(bundle) -> list[dict]:
    """Select reproducible evidence samples; never manufacture quality judgments."""
    selected = {}
    groups = {
        "person": [m for m in bundle.memories if m.entity],
        "dated_event": [m for m in bundle.memories if m.event_time],
        "time_expression": [m for m in bundle.memories if m.time_expression],
        "unknown_time": [m for m in bundle.memories if m.event_time is None],
    }
    histories = {}
    for memory in bundle.memories:
        if memory.entity and memory.attribute:
            histories.setdefault((memory.entity, memory.attribute), []).append(memory)
    groups["state_history"] = [
        m for group in histories.values() if len({m.value for m in group}) > 1 for m in group
    ]
    for category, memories in groups.items():
        # Spread samples over the conversation, rather than only its first session.
        for index in sorted({0, len(memories) // 2, len(memories) - 1}):
            if memories:
                memory = memories[index]
                selected.setdefault(memory.memory_id, (memory, []))[1].append(category)
    sources = {turn.key: turn for turn in bundle.source_turns}
    return [
        {
            "memory": memory.model_dump(mode="json"),
            "categories": categories,
            "evidence": [sources[source.key].model_dump(mode="json") for source in memory.sources],
            "review_status": "pending",
            "reviewer": None,
            "findings": None,
        }
        for memory, categories in selected.values()
    ]


def run(
    data: Path,
    output: Path,
    source_kind: str,
    config: BuildConfig,
    *,
    preflight=False,
    cache_only=False,
) -> int:
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8]
    run_dir = output / "runs" / run_id
    manifest_path = run_dir / "manifest.json"
    manifest = {
        "run_id": run_id,
        "started_at_utc": datetime.now(UTC).isoformat(),
        "command_argv": [sys.executable, "-m", "src.memory.devbuild", *sys.argv[1:]],
        "source_path": str(data),
        "source_kind": source_kind,
        "source_file_sha256": hashlib.sha256(data.read_bytes()).hexdigest(),
        "python_version": platform.python_version(),
        "config": config.model_dump(mode="json"),
        "config_id": config.config_id,
        "prompt_version": PROMPT_VERSION,
        "prompt_sha256": hashlib.sha256(PROMPT.encode()).hexdigest(),
        "base_url": os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        "status": "preflight",
        "conversations": [],
    }
    for conversation_id in DEVELOPMENT_IDS:
        turns = load_conversation(data, conversation_id)
        if not turns:
            raise ValueError(f"Empty development conversation: {conversation_id}")
        manifest["conversations"].append(
            {
                "conversation_id": conversation_id,
                "source_turn_count": len(turns),
                "block_count": len(list(chunks(turns, config))),
                "status": "pending",
                "events": [],
                "replay_events": [],
            }
        )
    write_json(manifest, manifest_path)
    print(f"Manifest: {manifest_path}", flush=True)
    if preflight:
        return 0
    if not cache_only and os.getenv("DEEPSEEK_API_KEY", "") in {"", "your_key_here"}:
        manifest["status"] = "blocked_missing_api_key"
        write_json(manifest, manifest_path)
        print("Set DEEPSEEK_API_KEY locally in .env; no API requests made.", flush=True)
        return 2

    manifest["status"] = "running"
    for item in manifest["conversations"]:
        conversation_id = item["conversation_id"]
        started = time.perf_counter()
        item["status"] = "running"
        write_json(manifest, manifest_path)

        def record(event, *, target="events", item=item, conversation_id=conversation_id):
            item[target].append(event)
            write_json(manifest, manifest_path)
            print(f"{conversation_id}: {target} {len(item[target])} {event['kind']}", flush=True)

        try:
            bundle = build_memories(
                data, conversation_id, config=config, cache_only=cache_only, on_event=record
            )
            memory_path = output / "memory" / f"{conversation_id}.json"
            save_memories(bundle, memory_path)
            if load_memories(memory_path) != bundle:
                raise RuntimeError("MemoryBundle serialization round-trip mismatch")
            item["build_elapsed_seconds"] = round(time.perf_counter() - started, 6)
            replay_started = time.perf_counter()
            replay = build_memories(
                data,
                conversation_id,
                config=config,
                cache_only=True,
                on_event=partial(record, target="replay_events"),
            )
            if replay != bundle:
                raise RuntimeError("Cache replay changed the MemoryBundle")
            item.update(
                {
                    "status": "complete",
                    "bundle_path": str(memory_path),
                    "build_info": bundle.build_info.model_dump(mode="json"),
                    "memory_count": len(bundle.memories),
                    "cache_replay_identical": True,
                    "cache_replay_seconds": round(time.perf_counter() - replay_started, 6),
                    "quality_review_status": "pending",
                }
            )
            write_json(
                {
                    "build_id": bundle.build_info.build_id,
                    "review_status": "pending",
                    "samples": review_samples(bundle),
                },
                run_dir / f"{conversation_id}-review.json",
            )
        except Exception as error:
            item["status"] = "failed"
            # Exception text may contain response input; persist the type only.
            item["error_type"] = type(error).__name__
            print(f"{conversation_id}: failed ({type(error).__name__}); see manifest", flush=True)
        finally:
            item["elapsed_seconds"] = round(time.perf_counter() - started, 6)
            attempts = [event for event in item["events"] if event["kind"] == "api_attempt"]
            item["api_attempt_count"] = len(attempts)
            item["usage_missing_attempts"] = sum(
                event.get("usage", {}).get("total_tokens") is None
                if isinstance(event.get("usage"), dict)
                else True
                for event in attempts
            )
            item["reported_total_tokens"] = sum(
                (event.get("usage") or {}).get("total_tokens", 0) for event in attempts
            )
            write_json(manifest, manifest_path)
    success = all(item["status"] == "complete" for item in manifest["conversations"])
    manifest["status"] = "builds_complete_review_pending" if success else "incomplete"
    manifest["finished_at_utc"] = datetime.now(UTC).isoformat()
    write_json(manifest, manifest_path)
    return 0 if success else 1


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--source-kind", choices=["course", "public"], required=True)
    parser.add_argument("--output", type=Path, default=Path("outputs/milestone5-b"))
    parser.add_argument("--cache-dir", type=Path, default=Path("cache"))
    parser.add_argument("--chunk-turns", type=int, default=20)
    parser.add_argument("--max-tokens", type=int, default=8192)
    parser.add_argument("--preflight", action="store_true")
    parser.add_argument("--cache-only", action="store_true")
    args = parser.parse_args()
    config = BuildConfig(
        model=os.getenv("DEEPSEEK_MODEL", "deepseek-flash"),
        cache_dir=args.cache_dir,
        chunk_turns=args.chunk_turns,
        max_tokens=args.max_tokens,
        timeout=float(os.getenv("DEEPSEEK_TIMEOUT", "120")),
    )
    raise SystemExit(
        run(
            args.data,
            args.output,
            args.source_kind,
            config,
            preflight=args.preflight,
            cache_only=args.cache_only,
        )
    )


if __name__ == "__main__":
    main()
