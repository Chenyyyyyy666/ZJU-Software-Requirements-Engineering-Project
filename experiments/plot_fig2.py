"""Generate Figure 2: a real state-evolution case from conv-30."""

import json
from pathlib import Path

import matplotlib
import matplotlib.pyplot as plt
from dotenv import load_dotenv
from matplotlib.patches import FancyArrowPatch

from src.common.models import MemoryBundle
from src.reasoning.relation_judge import judge_relations

# matplotlib 配置放在全部 import 之后
matplotlib.rcParams["font.sans-serif"] = ["SimHei", "Microsoft YaHei", "Arial Unicode MS"]
matplotlib.rcParams["axes.unicode_minus"] = False

load_dotenv()

BUNDLE = Path("outputs/memory/conv-30.json")
OUTPUT = Path("docs/figures/fig-02-state-evolution.png")

OLD_ID_PREFIX = "m_aaacfb32"
NEW_ID_PREFIX = "m_c5bdb6cd"


def main() -> None:
    bundle = MemoryBundle.model_validate(json.loads(BUNDLE.read_text(encoding="utf-8")))
    old = next(m for m in bundle.memories if m.memory_id.startswith(OLD_ID_PREFIX))
    new = next(m for m in bundle.memories if m.memory_id.startswith(NEW_ID_PREFIX))
    relations = judge_relations([old, new])
    if not relations:
        raise SystemExit("No relation produced; pair was skipped.")
    rel = relations[0]

    fig, ax = plt.subplots(figsize=(11, 5))
    ax.set_xlim(0, 10)
    ax.set_ylim(0, 6)
    ax.axis("off")

    # Timeline
    ax.plot([1, 9], [3, 3], color="#888888", linewidth=2, zorder=1)
    ax.scatter([2, 8], [3, 3], color="#333333", s=80, zorder=2)

    old_text = (
        f"旧 Memory  ({old.session_time})\n"
        f"session {old.sources[0].session_id}, {old.sources[0].turn_id}\n\n"
        f"{old.content}\n\n"
        f"entity: {old.entity}\n"
        f"attribute: {old.attribute}\n"
        f"value: {old.value}"
    )
    new_text = (
        f"新 Memory  ({new.session_time})\n"
        f"session {new.sources[0].session_id}, {new.sources[0].turn_id}\n\n"
        f"{new.content}\n\n"
        f"entity: {new.entity}\n"
        f"attribute: {new.attribute}\n"
        f"value: {new.value}"
    )

    ax.text(
        2,
        3.4,
        old_text,
        ha="center",
        va="bottom",
        fontsize=9,
        bbox={"boxstyle": "round,pad=0.6", "facecolor": "#f0f0f0", "edgecolor": "#666666"},
    )
    ax.text(
        8,
        3.4,
        new_text,
        ha="center",
        va="bottom",
        fontsize=9,
        bbox={"boxstyle": "round,pad=0.6", "facecolor": "#e8f4ff", "edgecolor": "#3366aa"},
    )

    arrow = FancyArrowPatch(
        (2.8, 3),
        (7.2, 3),
        arrowstyle="->",
        mutation_scale=20,
        color="#cc3333",
        linewidth=2,
        zorder=3,
    )
    ax.add_patch(arrow)
    ax.text(
        5,
        3.25,
        rel.relation_type,
        ha="center",
        va="bottom",
        fontsize=12,
        color="#cc3333",
        fontweight="bold",
    )
    ax.text(
        5,
        2.7,
        f"reason: {rel.reason}",
        ha="center",
        va="top",
        fontsize=8,
        style="italic",
        wrap=True,
    )

    ax.text(
        5,
        5.5,
        f"图 2：conv-30 中 Jon 就业状态的真实演化（build_id: {bundle.build_info.build_id[:12]}…）",
        ha="center",
        fontsize=11,
        fontweight="bold",
    )
    ax.text(
        5,
        0.6,
        "数据来源：outputs/memory/conv-30.json（llm_extraction, deepseek-flash, "
        "memory-facts-v1）\n关系判定由 src/reasoning/relation_judge.judge_relations "
        "实际运行产生；本图不涉及合成数据。",
        ha="center",
        fontsize=8,
        color="#555555",
    )

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUTPUT, dpi=200, bbox_inches="tight")
    print(f"Saved: {OUTPUT}")


if __name__ == "__main__":
    main()
