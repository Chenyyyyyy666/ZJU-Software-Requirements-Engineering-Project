"""Draw a source-backed Memory timeline, without inferring relation labels.

Example::

    python experiments/draw_state_example.py \
      --memory outputs/locomo-public/memory/conv-26.json \
      --output-dir docs/figures

The defaults identify three facts from the 958-Memory development build. A
changed build must supply its own --memory-id values rather than silently
reusing the old IDs. This script reads no QA or relation-label files.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import textwrap
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

DEFAULT_MEMORY_IDS = (
    "m_5e5e0af5adefa105a974cdcc808c516e37409e918d6d61a2dfadbeeac1ae0c93",
    "m_3bfa6e1817e405c86ff1224b2c59790115ebf700930836ec66ead2106660c30d",
    "m_e2a1d71fd1c12d5b6bdb6ae230244df8a4f331e3fd934cd92b0a1d5636184e70",
)


def read_example(path: Path, memory_ids: list[str]) -> dict[str, Any]:
    """Resolve selected Memory IDs and every referenced original source turn."""
    raw = path.read_bytes()
    bundle = json.loads(raw.decode("utf-8-sig"))
    if bundle.get("schema_version") != "0.1":
        raise ValueError("Expected MemoryBundle schema_version 0.1")
    memories = {item["memory_id"]: item for item in bundle["memories"]}
    turns = {(item["session_id"], item["turn_id"]): item for item in bundle["source_turns"]}
    if len(set(memory_ids)) != len(memory_ids):
        raise ValueError("Memory IDs must be unique")
    facts = []
    for memory_id in memory_ids:
        if memory_id not in memories:
            raise ValueError(
                f"Memory ID not in supplied build: {memory_id}; "
                "verify the build or pass --memory-id for each selected fact"
            )
        memory = memories[memory_id]
        if memory["conversation_id"] != bundle["conversation_id"]:
            raise ValueError("Cross-conversation Memory in supplied bundle")
        sources = []
        for ref in memory["sources"]:
            key = (ref["session_id"], ref["turn_id"])
            if key not in turns:
                raise ValueError(f"Missing source {key} for {memory_id}")
            turn = turns[key]
            if turn["conversation_id"] != bundle["conversation_id"]:
                raise ValueError("Cross-conversation source in supplied bundle")
            sources.append(turn)
        if not sources:
            raise ValueError(f"Memory has no original source: {memory_id}")
        facts.append({"memory": memory, "source_turns": sources})
    facts.sort(
        key=lambda item: (
            item["memory"].get("session_time") or "",
            item["source_turns"][-1]["session_id"],
            item["source_turns"][-1]["turn_id"],
        )
    )
    return {
        "figure": "fig-02-state-evolution",
        "kind": "source_fact_timeline",
        "conversation_id": bundle["conversation_id"],
        "build_info": bundle["build_info"],
        "bundle_file": path.name,
        "bundle_file_sha256": hashlib.sha256(raw).hexdigest(),
        "sample_size": {"memories": len(facts), "questions": 0},
        "facts": facts,
        "predicted_relations": [],
        "relation_evaluation_status": "not_run",
        "limitations": [
            "Arrows indicate source order, not system-predicted or human-gold relation labels.",
            "The final fact has a different extracted attribute, so the current exact "
            "(entity, attribute) grouping does not compare it with the first two.",
            "Session time is when a fact was said; it is not necessarily the event date.",
            "This qualitative example is not an accuracy or acceptance-test result.",
        ],
    }


def draw_figure(metadata: dict[str, Any], output_dir: Path) -> None:
    """Write a printable English-label timeline as PNG and editable SVG."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import FancyArrowPatch, FancyBboxPatch

    facts = metadata["facts"]
    if len(facts) != 3:
        raise ValueError("This report figure layout requires exactly three Memory IDs")
    plt.rcParams.update({"font.family": "DejaVu Sans", "svg.fonttype": "none"})
    fig, ax = plt.subplots(figsize=(16, 8.2), dpi=180)
    fig.patch.set_facecolor("white")
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")
    fig.subplots_adjust(left=0.025, right=0.975, top=0.96, bottom=0.045)
    ink, muted, accent = "#182d43", "#526579", "#2c647e"
    ax.text(
        0.02,
        0.97,
        f"Source-backed adoption timeline | {metadata['conversation_id']}",
        fontsize=23,
        fontweight="bold",
        color=ink,
        va="top",
    )
    ax.text(
        0.02,
        0.90,
        "Three extracted facts and their original evidence; no relation labels inferred",
        fontsize=13,
        color=muted,
    )
    xs, width = (0.02, 0.36, 0.70), 0.28
    for number, (x, fact) in enumerate(zip(xs, facts, strict=True), start=1):
        memory = fact["memory"]
        date = (memory.get("session_time") or "unknown")[:10]
        ax.add_patch(
            FancyBboxPatch(
                (x, 0.31),
                width,
                0.50,
                boxstyle="round,pad=0.012,rounding_size=0.012",
                facecolor="#f4f8fb",
                edgecolor="#bdccd7",
                linewidth=1.25,
            )
        )
        ax.text(
            x + 0.014,
            0.775,
            f"{number}  |  {date}",
            fontsize=15,
            fontweight="bold",
            color=accent,
            va="top",
        )
        ax.text(
            x + 0.014,
            0.715,
            textwrap.fill(memory["content"], 35),
            fontsize=14,
            color=ink,
            va="top",
            linespacing=1.45,
        )
        ax.text(
            x + 0.014,
            0.575,
            "Attribute\n" + textwrap.fill(str(memory.get("attribute")), 34),
            fontsize=10.5,
            color=muted,
            va="top",
            linespacing=1.4,
        )
        source = fact["source_turns"][0]
        quote = " ".join(source["text"].split())
        excerpt = textwrap.shorten(quote, width=165, placeholder=" …")
        ax.text(
            x + 0.014,
            0.475,
            f"Original source: {source['turn_id']}",
            fontsize=11,
            fontweight="bold",
            color=ink,
            va="top",
        )
        ax.text(
            x + 0.014,
            0.432,
            textwrap.fill(excerpt, 43),
            fontsize=10.1,
            color=muted,
            va="top",
            linespacing=1.35,
        )
        ax.text(
            x + 0.014,
            0.285,
            f"Memory ID: {memory['memory_id'][:18]}…",
            fontsize=9,
            color=muted,
            va="top",
        )
    for left, right in zip(xs[:-1], xs[1:], strict=True):
        ax.add_patch(
            FancyArrowPatch(
                (left + width + 0.018, 0.665),
                (right - 0.018, 0.665),
                arrowstyle="-|>",
                mutation_scale=14,
                color="#8093a3",
                linewidth=1.5,
            )
        )
    ax.text(
        0.50,
        0.205,
        "Arrows show later source order, not RETAIN / UPDATE / SUPERSEDE / CONFLICT.",
        fontsize=12,
        color=ink,
        ha="center",
        fontweight="bold",
    )
    ax.text(
        0.02,
        0.14,
        "Implementation limit: adoption_interview_status differs from adoption_status; "
        "exact attribute grouping may split this history.",
        fontsize=11.2,
        color=muted,
    )
    ax.text(
        0.02,
        0.095,
        f"Build: {metadata['build_info']['build_id'][:24]}…  |  "
        f"Config: {metadata['build_info']['extractor_config_id']}  |  "
        f"Generated: {metadata['generated_date']}",
        fontsize=10,
        color=muted,
    )
    ax.text(
        0.02,
        0.05,
        "Source: supplied MemoryBundle and original SourceTurns. Full Memory IDs, evidence "
        "and limitations are saved in the companion JSON.",
        fontsize=10,
        color=muted,
    )
    for extension in ("png", "svg"):
        fig.savefig(output_dir / f"fig-02-state-evolution.{extension}", dpi=220, facecolor="white")
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--memory", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--memory-id", action="append", dest="memory_ids")
    parser.add_argument("--generated-date", default=datetime.now(UTC).date().isoformat())
    args = parser.parse_args()
    metadata = read_example(args.memory, args.memory_ids or list(DEFAULT_MEMORY_IDS))
    metadata["generated_date"] = args.generated_date
    metadata["generator"] = "experiments/draw_state_example.py"
    args.output_dir.mkdir(parents=True, exist_ok=True)
    draw_figure(metadata, args.output_dir)
    output = args.output_dir / "fig-02-state-evolution.json"
    output.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote source timeline PNG, SVG and metadata to {args.output_dir}")


if __name__ == "__main__":
    main()
