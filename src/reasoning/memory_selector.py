"""Select currently valid memories given judged relations."""

from src.common.models import Memory, MemoryRelation


def select_effective(
    memories: list[Memory],
    relations: list[MemoryRelation],
    *,
    time_scope: str = "current",
) -> list[Memory]:
    superseded: set[str] = set()
    for relation in relations:
        if relation.relation_type == "SUPERSEDE" and time_scope != "past":
            superseded.add(relation.memory_a)
    return [m for m in memories if m.memory_id not in superseded]


def has_conflict(relations: list[MemoryRelation]) -> bool:
    return any(r.relation_type == "CONFLICT" for r in relations)