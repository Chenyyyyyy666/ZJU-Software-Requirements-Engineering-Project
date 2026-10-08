"""Build a validated memory bundle: python -m src.memory.build --help."""

import argparse
import os
from collections.abc import Iterator
from itertools import groupby
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from pydantic import Field

from src.common.json_io import digest
from src.common.models import (
    SCHEMA_VERSION,
    BuildInfo,
    Contract,
    Fact,
    Memory,
    MemoryBundle,
    PositiveInt,
    SourceRef,
    SourceTurn,
)
from src.memory.extract import PROMPT, PROMPT_VERSION, DeepSeekExtractor
from src.memory.locomo import load_conversation
from src.memory.store import save_memories


class BuildConfig(Contract):
    mode: Literal["deepseek", "turns"] = "deepseek"
    model: str = "deepseek-flash"
    chunk_turns: PositiveInt = 20
    chunk_chars: PositiveInt = 12000
    cache_dir: Path = Path("cache")
    timeout: float = Field(default=60.0, gt=0, allow_inf_nan=False)
    max_attempts: PositiveInt = 3
    max_tokens: PositiveInt = 4096

    @property
    def config_id(self) -> str:
        settings = self.model_dump(mode="json", exclude={"cache_dir", "timeout", "max_attempts"})
        return digest({"settings": settings, "adapter_version": "locomo-v1"})[:16]


def chunks(turns: list[SourceTurn], config: BuildConfig) -> Iterator[list[SourceTurn]]:
    for _, session in groupby(turns, key=lambda turn: turn.session_id):
        block: list[SourceTurn] = []
        size = 0
        for turn in session:
            if len(turn.text) > config.chunk_chars:
                raise ValueError(
                    f"Turn {turn.turn_id} exceeds chunk_chars; increase the limit, do not truncate"
                )
            if block and (
                len(block) >= config.chunk_turns or size + len(turn.text) > config.chunk_chars
            ):
                yield block
                block, size = [], 0
            block.append(turn)
            size += len(turn.text)
        if block:
            yield block


def build_memories(data_path: Path, conversation_id: str, *, config: BuildConfig) -> MemoryBundle:
    turns = load_conversation(data_path, conversation_id)
    input_hash = digest([turn.model_dump(mode="json") for turn in turns])
    extractor = None
    if config.mode == "deepseek":
        extractor = DeepSeekExtractor(
            model=config.model,
            cache_dir=config.cache_dir,
            config_id=config.config_id,
            timeout=config.timeout,
            max_attempts=config.max_attempts,
            max_tokens=config.max_tokens,
        )
    facts = []
    for block_number, block in enumerate(chunks(turns, config), 1):
        if extractor is not None:
            try:
                facts.extend(extractor.extract(block))
            except RuntimeError:
                raise RuntimeError(
                    f"Extraction failed in block {block_number}, session {block[0].session_id}; "
                    "completed blocks remain cached, no complete bundle was saved"
                ) from None
        else:
            # A clearly labelled offline baseline, not semantic entity extraction.
            facts.extend(
                Fact(
                    content=turn.text,
                    entity=None,
                    attribute=None,
                    value=None,
                    time_expression=None,
                    event_time=None,
                    event_time_precision="unknown",
                    sources=[SourceRef(session_id=turn.session_id, turn_id=turn.turn_id)],
                )
                for turn in block
            )
    order = {turn.key: i for i, turn in enumerate(turns)}
    memories = []
    seen = set()
    for fact in facts:
        fact_data = fact.model_dump(mode="json")
        fingerprint = digest(fact_data)
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        memories.append(
            Memory(
                **fact_data,
                memory_id=f"m_{fingerprint}",
                conversation_id=conversation_id,
                session_time=turns[order[fact.sources[-1].key]].session_time,
            )
        )
    build_id = digest(
        {
            "schema": SCHEMA_VERSION,
            "conversation_id": conversation_id,
            "input": input_hash,
            "config": config.config_id,
            "prompt": PROMPT if extractor else None,
            "memories": [memory.model_dump(mode="json") for memory in memories],
        }
    )
    return MemoryBundle(
        schema_version=SCHEMA_VERSION,
        conversation_id=conversation_id,
        build_info=BuildInfo(
            build_id=build_id,
            method="llm_extraction" if extractor else "turn_baseline",
            input_sha256=input_hash,
            extractor_config_id=config.config_id,
            model=config.model if extractor else None,
            prompt_version=PROMPT_VERSION if extractor else None,
            note=None if extractor else "Offline raw-turn baseline; no semantic extraction.",
        ),
        source_turns=turns,
        memories=memories,
    )


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data", type=Path, default=Path(os.getenv("LOCOMO_DATA_PATH", "locomo.json"))
    )
    parser.add_argument("--conversation", required=True)
    parser.add_argument("--mode", choices=["deepseek", "turns"], default="deepseek")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--chunk-turns", type=int, default=20)
    parser.add_argument("--chunk-chars", type=int, default=12000)
    args = parser.parse_args()
    config = BuildConfig(
        mode=args.mode,
        model=os.getenv("DEEPSEEK_MODEL", "deepseek-flash"),
        chunk_turns=args.chunk_turns,
        chunk_chars=args.chunk_chars,
        cache_dir=Path(os.getenv("CACHE_DIR", "cache")),
        timeout=float(os.getenv("DEEPSEEK_TIMEOUT", "60")),
    )
    # IDs supplied by external data never become unvalidated filesystem paths.
    safe_id = args.conversation
    if args.output is None and (
        not safe_id
        or any(
            c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
            for c in safe_id
        )
    ):
        parser.error("Unsafe conversation ID for default output path; specify --output")
    output = args.output or Path("outputs/memory") / f"{safe_id}.json"
    bundle = build_memories(args.data, args.conversation, config=config)
    save_memories(bundle, output)
    print(f"{bundle.build_info.method}: {len(bundle.memories)} memories saved to {output}")


if __name__ == "__main__":
    main()
