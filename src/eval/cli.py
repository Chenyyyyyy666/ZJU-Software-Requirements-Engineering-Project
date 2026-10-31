"""Course evaluation CLI.

Full answer generation requires a MemoryBundle and DEEPSEEK_API_KEY. For offline
work, use --answers-input with recorded answers and evidence; this still computes
F1, Recall@K, relation accuracy and average token without calling a model.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from src.common.json_io import write_json
from src.common.models import RetrievalRequest
from src.eval.metrics import evaluate_rows


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _questions_from_payload(payload: Any, conversation: str) -> list[dict[str, Any]]:
    if isinstance(payload, dict) and isinstance(payload.get("questions"), list):
        return [dict(item) for item in payload["questions"]]
    if isinstance(payload, dict) and isinstance(payload.get("qa"), list):
        return [dict(item) for item in payload["qa"]]
    if isinstance(payload, list):
        if payload and all(isinstance(item, dict) and "sample_id" in item for item in payload):
            for sample in payload:
                if sample.get("sample_id") == conversation:
                    return [dict(item) for item in sample.get("qa", [])]
            raise ValueError(f"conversation not found in questions file: {conversation}")
        return [dict(item) for item in payload]
    raise ValueError("questions JSON must be a list or contain questions/qa")


def _answer_rows(payload: Any, conversation: str) -> list[dict[str, Any]]:
    if isinstance(payload, dict) and isinstance(payload.get("answers"), list):
        return [dict(item) for item in payload["answers"]]
    if isinstance(payload, list):
        return [dict(item) for item in payload]
    return _questions_from_payload(payload, conversation)


def _question_id(question: dict[str, Any], index: int) -> str:
    return str(question.get("question_id", question.get("id", f"q-{index + 1}")))


def _question_text(question: dict[str, Any]) -> str:
    value = question.get("question", question.get("query"))
    if not isinstance(value, str) or not value.strip():
        raise ValueError("each question needs a non-empty question/query field")
    return value.strip()


def _record_offline_answers(
    questions: list[dict[str, Any]], conversation: str
) -> list[dict[str, Any]]:
    records = []
    for index, question in enumerate(questions):
        answer = question.get(
            "answer", question.get("prediction", question.get("generated_answer"))
        )
        if not isinstance(answer, str) or not answer.strip():
            raise ValueError(
                "offline mode requires answer/prediction/generated_answer in every row; "
                "use --memory for live model execution"
            )
        records.append(
            {
                "question_id": _question_id(question, index),
                "conversation_id": conversation,
                "question": _question_text(question),
                "answer": answer.strip(),
                "gold_answer": question.get("gold_answer", question.get("reference_answer")),
                "gold_evidence": question.get("gold_evidence", question.get("evidence")),
                "gold_relation": question.get("gold_relation", question.get("relation_gold")),
                "hits": question.get("hits", []),
                "relation": question.get("relation", question.get("predicted_relation")),
                "token_usage": question.get("token_usage", question.get("tokens")),
            }
        )
    return records


def _run_live(
    questions: list[dict[str, Any]],
    conversation: str,
    memory_path: Path,
    mode: str,
    top_k: int,
    candidate_k: int,
    device: str,
    local_files_only: bool,
) -> list[dict[str, Any]]:
    from src.index.bm25 import IndexConfig, build_index, search_memories
    from src.memory.store import load_memories
    from src.reasoning.pipeline import answer

    bundle = load_memories(memory_path)
    if bundle.conversation_id != conversation:
        raise ValueError("--conversation does not match the MemoryBundle")
    index = build_index(
        bundle,
        config=IndexConfig(
            mode=mode,
            embedding_device=device,
            model_cache_dir=Path("cache/embedding_models"),
            local_files_only=local_files_only,
        ),
    )
    records = []
    for index_number, question in enumerate(questions):
        text = _question_text(question)
        request = RetrievalRequest(
            conversation_id=conversation,
            query=text,
            top_k=top_k,
            candidate_k=candidate_k,
            mode=mode,
            entity=question.get("entity"),
            attribute=question.get("attribute"),
        )
        retrieval = search_memories(index, request)
        answer_record = answer(text, retrieval, question_id=_question_id(question, index_number))
        records.append(
            {
                "question_id": answer_record.question_id,
                "conversation_id": conversation,
                "question": text,
                "answer": answer_record.answer,
                "valid_memory_ids": answer_record.valid_memory_ids,
                "conflict": answer_record.conflict,
                "build_id": answer_record.build_id,
                "gold_answer": question.get("gold_answer", question.get("answer")),
                "gold_evidence": question.get("gold_evidence", question.get("evidence")),
                "gold_relation": question.get("gold_relation", question.get("relation_gold")),
                "retrieval": retrieval.model_dump(mode="json"),
            }
        )
    return records


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--conversation", required=True)
    parser.add_argument("--questions", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--memory", type=Path, help="MemoryBundle JSON for live answer generation")
    parser.add_argument(
        "--answers-input",
        type=Path,
        help="Recorded answer/retrieval rows for offline metric calculation",
    )
    parser.add_argument("--metrics-output", type=Path)
    parser.add_argument("--mode", choices=["bm25", "vector", "hybrid"], default="bm25")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--candidate-k", type=int, default=30)
    parser.add_argument("--device", choices=["cpu", "cuda", "mps"], default="cpu")
    parser.add_argument("--local-files-only", action="store_true")
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    if args.top_k <= 0 or args.candidate_k < args.top_k:
        parser.error("--top-k must be positive and <= --candidate-k")
    questions = _questions_from_payload(_load_json(args.questions), args.conversation)
    if args.answers_input is not None:
        rows = _record_offline_answers(
            _answer_rows(_load_json(args.answers_input), args.conversation), args.conversation
        )
    elif args.memory is not None:
        rows = _run_live(
            questions,
            args.conversation,
            args.memory,
            args.mode,
            args.top_k,
            args.candidate_k,
            args.device,
            args.local_files_only,
        )
    else:
        parser.error(
            "provide --memory for live execution or --answers-input for offline evaluation"
        )
    write_json(rows, args.output)
    if args.metrics_output is not None:
        write_json(evaluate_rows(rows), args.metrics_output)
    print(f"wrote {len(rows)} rows to {args.output}")


if __name__ == "__main__":
    main()
