"""Judge temporal relations between candidate memories."""

import json
import os

from openai import OpenAI

from src.common.models import Memory, MemoryRelation, SourceRef

PROMPT_VERSION = "relation_v1"

SYSTEM_PROMPT = """Compare two memories about the same entity and attribute.
Decide the relation of memory B to memory A:
- RETAIN: both can be true at the same time
- UPDATE: B adds to or refines A
- SUPERSEDE: B replaces A
- CONFLICT: cannot both be true and no time evidence resolves it

If both memories lack any time information, output CONFLICT.
Output JSON only: {"relation_type": "...", "reason": "..."}"""


def _client() -> OpenAI:
    return OpenAI(
        api_key=os.environ["DEEPSEEK_API_KEY"],
        base_url=os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
    )


def _render(memory: Memory) -> str:
    return json.dumps(
        {
            "content": memory.content,
            "entity": memory.entity,
            "attribute": memory.attribute,
            "session_time": memory.session_time,
            "event_time": memory.event_time,
        },
        ensure_ascii=False,
    )


def _has_no_time(memory: Memory) -> bool:
    return memory.event_time is None and memory.session_time is None


def _merge_sources(a: Memory, b: Memory) -> list[SourceRef]:
    seen: set[tuple[int, str]] = set()
    merged: list[SourceRef] = []
    for ref in [*a.sources, *b.sources]:
        if ref.key not in seen:
            seen.add(ref.key)
            merged.append(ref)
    return merged


def judge_pair(a: Memory, b: Memory, *, max_attempts: int = 2) -> MemoryRelation:
    sources = _merge_sources(a, b)
    if _has_no_time(a) and _has_no_time(b):
        return MemoryRelation(
            memory_a=a.memory_id,
            memory_b=b.memory_id,
            relation_type="CONFLICT",
            reason="both memories lack time information",
            sources=sources,
        )
    last_error: Exception | None = None
    for _ in range(max_attempts):
        resp = _client().chat.completions.create(
            model=os.environ.get("DEEPSEEK_MODEL", "deepseek-flash"),
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": f"Memory A: {_render(a)}\nMemory B: {_render(b)}",
                },
            ],
            response_format={"type": "json_object"},
            extra_body={"thinking": {"type": "disabled"}},
            max_tokens=512,
        )
        raw = resp.choices[0].message.content
        if not raw:
            last_error = ValueError("empty content")
            continue
        try:
            data = json.loads(raw)
            return MemoryRelation(
                memory_a=a.memory_id,
                memory_b=b.memory_id,
                relation_type=data["relation_type"],
                reason=data["reason"],
                sources=sources,
            )
        except (json.JSONDecodeError, KeyError) as exc:
            last_error = exc
    return MemoryRelation(
        memory_a=a.memory_id,
        memory_b=b.memory_id,
        relation_type="CONFLICT",
        reason=f"judge failed: {last_error}",
        sources=sources,
    )


def judge_relations(memories: list[Memory]) -> list[MemoryRelation]:
    groups: dict[tuple[str, str], list[Memory]] = {}
    for memory in memories:
        if memory.entity is None or memory.attribute is None:
            continue
        groups.setdefault((memory.entity, memory.attribute), []).append(memory)

    relations: list[MemoryRelation] = []
    for group in groups.values():
        for i in range(len(group)):
            for j in range(i + 1, len(group)):
                relations.append(judge_pair(group[i], group[j]))
    return relations