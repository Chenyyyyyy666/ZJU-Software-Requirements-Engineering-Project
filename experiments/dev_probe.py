"""Run the full reasoning pipeline on the dev split and save every intermediate result.

Usage (from repo root):
    python experiments/dev_probe.py --version v1_bm25 --mode bm25 --limit-per-conv 3
    python experiments/dev_probe.py --version v1_hybrid --mode hybrid --skip-cat 5

Gold answers/evidence are only written into the output record for later
evaluation; they are never passed to any pipeline function.
"""

import argparse
import json
import sys
from functools import cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.common.models import MemoryBundle, RetrievalRequest  # noqa: E402
from src.index import IndexConfig, build_index, search_memories  # noqa: E402
from src.reasoning import answer_generator, query_analyzer, relation_judge  # noqa: E402
from src.reasoning.memory_selector import has_conflict, select_effective  # noqa: E402

MEMORY_DIR = ROOT / "outputs" / "memory"
SPLITS_PATH = ROOT / "experiments" / "splits.json"
DATA_PATH = ROOT / "locomo.json"


@cache
def _load_index(conv_id: str, mode: str):
    path = MEMORY_DIR / f"{conv_id}.json"
    bundle = MemoryBundle.model_validate_json(path.read_text(encoding="utf-8"))
    return build_index(bundle, config=IndexConfig(mode=mode))


def retrieve(conv_id: str, question: str, *, mode: str, top_k: int):
    index = _load_index(conv_id, mode)
    request = RetrievalRequest(
        conversation_id=index.bundle.conversation_id,
        query=question,
        top_k=top_k,
        candidate_k=max(30, top_k),
        mode=mode,
    )
    return search_memories(index, request)


def dump(obj):
    return obj.model_dump() if hasattr(obj, "model_dump") else obj


def run_pipeline(conv_id: str, question: str, qid: str, *, mode: str, top_k: int) -> dict:
    retrieval = retrieve(conv_id, question, mode=mode, top_k=top_k)
    qa = query_analyzer.analyze_query(question, conv_id, retrieval.build_id)
    memories = [h.memory for h in retrieval.hits]

    relations = relation_judge.judge_relations(memories)
    effective = select_effective(memories, relations, time_scope=qa.time_scope)
    conflict = has_conflict(relations)
    ans = answer_generator.generate_answer(
        question,
        effective,
        conflict=conflict,
        build_id=retrieval.build_id,
        question_id=qid,
    )

    groups: dict[str, list[str]] = {}
    for m in memories:
        if m.entity and m.attribute:
            groups.setdefault(f"{m.entity}|{m.attribute}", []).append(m.memory_id)

    return {
        "mode": mode,
        "query_analysis": dump(qa),
        "retrieved": [
            {
                "rank": h.rank,
                "score": h.score,
                "channel_scores": {k: dump(v) for k, v in h.channel_scores.items()},
                "memory": dump(h.memory),
            }
            for h in retrieval.hits
        ],
        "groups": groups,
        "relations": [dump(r) for r in relations],
        "effective_ids": [m.memory_id for m in effective],
        "conflict": conflict,
        "answer": ans.answer,
        "judge_failures": sum(r.reason.startswith("judge failed") for r in relations),
        "prompt_versions": {
            "query": query_analyzer.PROMPT_VERSION,
            "relation": relation_judge.PROMPT_VERSION,
            "answer": answer_generator.PROMPT_VERSION,
        },
    }


def _load_done(out: Path) -> set[str]:
    if not out.exists():
        return set()
    done = set()
    for line in out.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        if "error" not in rec:  # failed questions are retried on the next run
            done.add(rec["qid"])
    return done


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", required=True, help="tag used in the output file name")
    ap.add_argument("--mode", choices=["bm25", "vector", "hybrid"], default="hybrid")
    ap.add_argument("--top-k", type=int, default=10)
    ap.add_argument("--limit-per-conv", type=int, default=0, help="0 = all questions")
    ap.add_argument("--skip-cat", type=int, nargs="*", default=[])
    ap.add_argument("--convs", nargs="*", help="override the dev conversations")
    args = ap.parse_args()

    splits = json.loads(SPLITS_PATH.read_text(encoding="utf-8-sig"))
    data = {d["sample_id"]: d for d in json.loads(DATA_PATH.read_text(encoding="utf-8-sig"))}
    conv_ids = args.convs or splits["dev"]

    out = ROOT / "experiments" / "results" / f"dev_probe_{args.version}.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    done = _load_done(out)

    n_ok = n_err = 0
    with out.open("a", encoding="utf-8") as f:
        for cid in conv_ids:
            # number first, filter second, so qid always matches the position in locomo.json
            qas = [
                (i, q)
                for i, q in enumerate(data[cid]["qa"])
                if q.get("category") not in args.skip_cat
            ]
            if args.limit_per_conv:
                qas = qas[: args.limit_per_conv]
            for i, qa in qas:
                qid = f"{cid}-{i}"
                if qid in done:
                    continue
                try:
                    res = run_pipeline(cid, qa["question"], qid, mode=args.mode, top_k=args.top_k)
                    n_ok += 1
                except Exception as e:  # one bad question must not stop the batch
                    res = {"error": repr(e)}
                    n_err += 1
                rec = {
                    "qid": qid,
                    "conv": cid,
                    "category": qa.get("category"),
                    "question": qa["question"],
                    "gold_answer": qa.get("answer"),  # evaluation only
                    "gold_evidence": qa.get("evidence"),  # evaluation only
                    "version": args.version,
                    **res,
                }
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                f.flush()
                print("done" if "error" not in res else "ERROR", qid)

    print(f"finished: ok={n_ok} error={n_err} -> {out}")


if __name__ == "__main__":
    main()