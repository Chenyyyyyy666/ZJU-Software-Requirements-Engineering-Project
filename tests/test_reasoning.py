"""Tests for the reasoning module: query analysis, relation judging, selection, answer."""

import json
from unittest.mock import MagicMock, patch

import pytest

from src.common.models import (
    AnswerRecord,
    Memory,
    MemoryRelation,
    QueryAnalysisResult,
    SourceRef,
)
from src.reasoning.answer_generator import generate_answer
from src.reasoning.memory_selector import has_conflict, select_effective
from src.reasoning.query_analyzer import analyze_query
from src.reasoning.relation_judge import judge_pair, judge_relations


def _source(session_id: int, turn_id: str) -> SourceRef:
    return SourceRef(session_id=session_id, turn_id=turn_id)


def _memory(
    memory_id: str,
    content: str,
    *,
    entity: str | None = "Caroline",
    attribute: str | None = "residence",
    value: str | None = None,
    session_time: str | None = "2023-10-01T09:00:00",
    event_time: str | None = "2023-10-01",
    precision: str = "day",
    sources: list[SourceRef] | None = None,
) -> Memory:
    return Memory(
        memory_id=memory_id,
        conversation_id="test-01",
        content=content,
        entity=entity,
        attribute=attribute,
        value=value,
        session_time=session_time,
        time_expression=None,
        event_time=event_time,
        event_time_precision=precision,
        sources=sources or [_source(1, "D1:1")],
    )


def _relation(
    relation_type: str, memory_a: str = "mem_old", memory_b: str = "mem_new"
) -> MemoryRelation:
    return MemoryRelation(
        memory_a=memory_a,
        memory_b=memory_b,
        relation_type=relation_type,
        reason="test",
        sources=[_source(1, "D1:1")],
    )


# ---------- query_analyzer ----------


def test_analyze_query_parses_valid_json():
    fake_resp = MagicMock()
    fake_resp.choices[0].message.content = json.dumps(
        {
            "entity": ["Caroline"],
            "time_scope": "current",
            "time_expression": None,
            "attribute": "residence",
            "confidence": 0.9,
        }
    )
    with patch("src.reasoning.query_analyzer._client") as mock_client:
        mock_client.return_value.chat.completions.create.return_value = fake_resp
        result = analyze_query("Where does Caroline live?", "test-01", "build-1")

    assert isinstance(result, QueryAnalysisResult)
    assert result.entity == ["Caroline"]
    assert result.time_scope == "current"
    assert result.attribute == "residence"
    assert result.conversation_id == "test-01"
    assert result.build_id == "build-1"


def test_analyze_query_retries_on_empty_content_then_fails():
    fake_resp = MagicMock()
    fake_resp.choices[0].message.content = ""
    with patch("src.reasoning.query_analyzer._client") as mock_client:
        mock_client.return_value.chat.completions.create.return_value = fake_resp
        with pytest.raises(ValueError, match="failed after"):
            analyze_query("q", "test-01", "build-1", max_attempts=2)


def test_analyze_query_rejects_invalid_time_scope():
    fake_resp = MagicMock()
    fake_resp.choices[0].message.content = json.dumps(
        {
            "entity": [],
            "time_scope": "future",
            "time_expression": None,
            "attribute": None,
            "confidence": 0.5,
        }
    )
    with patch("src.reasoning.query_analyzer._client") as mock_client:
        mock_client.return_value.chat.completions.create.return_value = fake_resp
        with pytest.raises(ValueError, match="failed after"):
            analyze_query("q", "test-01", "build-1", max_attempts=1)


# ---------- relation_judge ----------


def test_judge_relations_skips_null_entity_or_attribute():
    memories = [
        _memory("m1", "text one", entity=None, attribute=None),
        _memory("m2", "text two", entity=None, attribute=None),
        _memory("m3", "text three", entity="Caroline", attribute=None),
    ]
    assert judge_relations(memories) == []


def test_judge_relations_groups_same_entity_and_attribute():
    a = _memory("m1", "lives in Boston", value="Boston")
    b = _memory("m2", "lives in New York", value="New York")
    c = _memory("m3", "works at Acme", attribute="job", sources=[_source(2, "D2:1")])

    fake_resp = MagicMock()
    fake_resp.choices[0].message.content = json.dumps(
        {"relation_type": "SUPERSEDE", "reason": "newer residence"}
    )
    with patch("src.reasoning.relation_judge._client") as mock_client:
        mock_client.return_value.chat.completions.create.return_value = fake_resp
        relations = judge_relations([a, b, c])

    # only a and b share (Caroline, residence); c is a different attribute
    assert len(relations) == 1
    assert relations[0].memory_a == "m1"
    assert relations[0].memory_b == "m2"
    assert relations[0].relation_type == "SUPERSEDE"


def test_judge_pair_forces_conflict_when_both_lack_time():
    a = _memory("m1", "lives in Boston", session_time=None, event_time=None, precision="unknown")
    b = _memory("m2", "lives in New York", session_time=None, event_time=None, precision="unknown")
    relation = judge_pair(a, b)
    assert relation.relation_type == "CONFLICT"
    assert "time" in relation.reason.lower()


def test_judge_pair_deduplicates_sources():
    shared = _source(1, "D1:1")
    a = _memory("m1", "fact a", sources=[shared])
    b = _memory("m2", "fact b", sources=[shared, _source(2, "D2:1")])
    relation = judge_pair(a, b)
    keys = [(s.session_id, s.turn_id) for s in relation.sources]
    assert len(keys) == len(set(keys))


def test_judge_pair_falls_back_to_conflict_on_bad_response():
    a = _memory("m1", "fact a")
    b = _memory("m2", "fact b")
    fake_resp = MagicMock()
    fake_resp.choices[0].message.content = "not json"
    with patch("src.reasoning.relation_judge._client") as mock_client:
        mock_client.return_value.chat.completions.create.return_value = fake_resp
        relation = judge_pair(a, b, max_attempts=1)
    assert relation.relation_type == "CONFLICT"
    assert "judge failed" in relation.reason


# ---------- memory_selector ----------


def test_select_effective_drops_superseded_on_current_scope():
    memories = [_memory("mem_old", "Boston"), _memory("mem_new", "New York")]
    relations = [_relation("SUPERSEDE")]
    kept = select_effective(memories, relations, time_scope="current")
    assert [m.memory_id for m in kept] == ["mem_new"]


def test_select_effective_keeps_superseded_on_past_scope():
    memories = [_memory("mem_old", "Boston"), _memory("mem_new", "New York")]
    relations = [_relation("SUPERSEDE")]
    kept = select_effective(memories, relations, time_scope="past")
    assert {m.memory_id for m in kept} == {"mem_old", "mem_new"}


def test_select_effective_keeps_retain_and_update():
    memories = [_memory("m1", "a"), _memory("m2", "b")]
    relations = [_relation("RETAIN", "m1", "m2"), _relation("UPDATE", "m1", "m2")]
    kept = select_effective(memories, relations, time_scope="current")
    assert len(kept) == 2


def test_has_conflict_true_when_any_conflict():
    relations = [_relation("RETAIN"), _relation("CONFLICT")]
    assert has_conflict(relations) is True


def test_has_conflict_false_for_empty():
    assert has_conflict([]) is False


# ---------- answer_generator ----------


def test_generate_answer_returns_answer_record():
    memories = [_memory("m1", "Caroline lives in Boston.")]
    fake_resp = MagicMock()
    fake_resp.choices[0].message.content = "Caroline lives in Boston."
    with patch("src.reasoning.answer_generator._client") as mock_client:
        mock_client.return_value.chat.completions.create.return_value = fake_resp
        record = generate_answer(
            "Where does Caroline live?",
            memories,
            conflict=False,
            build_id="build-1",
            question_id="qa-1",
        )
    assert isinstance(record, AnswerRecord)
    assert record.answer == "Caroline lives in Boston."
    assert record.valid_memory_ids == ["m1"]
    assert record.conflict is False
    assert record.build_id == "build-1"
    assert record.question_id == "qa-1"


def test_generate_answer_marks_conflict_in_prompt():
    memories = [_memory("m1", "Caroline lives in Boston.")]
    fake_resp = MagicMock()
    fake_resp.choices[0].message.content = "There are conflicting reports."
    with patch("src.reasoning.answer_generator._client") as mock_client:
        create = mock_client.return_value.chat.completions.create
        create.return_value = fake_resp
        record = generate_answer(
            "Where does Caroline live?", memories, conflict=True, build_id="build-1"
        )
    sent_user_message = create.call_args.kwargs["messages"][1]["content"]
    assert "conflicting" in sent_user_message.lower()
    assert record.conflict is True


def test_generate_answer_falls_back_when_empty_content():
    memories = [_memory("m1", "fact")]
    fake_resp = MagicMock()
    fake_resp.choices[0].message.content = None
    with patch("src.reasoning.answer_generator._client") as mock_client:
        mock_client.return_value.chat.completions.create.return_value = fake_resp
        record = generate_answer("q", memories, conflict=False, build_id="build-1")
    assert record.answer == "(no answer)"