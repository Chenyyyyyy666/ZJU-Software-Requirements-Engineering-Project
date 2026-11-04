"""End-to-end reasoning pipeline: analyze, judge, select, answer."""

from src.common.models import AnswerRecord, MemoryRelation, QueryAnalysisResult, RetrievalResult
from src.reasoning.answer_generator import generate_answer
from src.reasoning.memory_selector import has_conflict, select_effective
from src.reasoning.query_analyzer import analyze_query
from src.reasoning.relation_judge import judge_relations


def answer(
    question: str, retrieval: RetrievalResult, *, question_id: str | None = None
) -> AnswerRecord:
    record, _, _ = answer_with_details(question, retrieval, question_id=question_id)
    return record


def answer_with_details(
    question: str, retrieval: RetrievalResult, *, question_id: str | None = None
) -> tuple[AnswerRecord, QueryAnalysisResult, list[MemoryRelation]]:
    """Return the same answer plus the query analysis and judged relation trace."""
    qa = analyze_query(question, retrieval.conversation_id, retrieval.build_id)
    memories = [hit.memory for hit in retrieval.hits]

    relations = judge_relations(memories)
    effective = select_effective(memories, relations, time_scope=qa.time_scope)
    conflict = has_conflict(relations)

    record = generate_answer(
        question,
        effective,
        conflict=conflict,
        build_id=retrieval.build_id,
        question_id=question_id,
    )
    return record, qa, relations
