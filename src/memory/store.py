"""Validated persistence and read-only evidence lookup."""

from pathlib import Path

from src.common.json_io import write_json
from src.common.models import Memory, MemoryBundle, RelatedMemoryResult, SourceRef, SourceTurn


def save_memories(bundle: MemoryBundle, path: Path) -> None:
    # Revalidate nested lists, including changes made after initial construction.
    validated = MemoryBundle.model_validate(bundle.model_dump())
    write_json(validated.model_dump(mode="json"), path)


def load_memories(path: Path) -> MemoryBundle:
    return MemoryBundle.model_validate_json(path.read_text(encoding="utf-8"))


def get_memory(bundle: MemoryBundle, memory_id: str) -> Memory:
    for memory in bundle.memories:
        if memory.memory_id == memory_id:
            return memory.model_copy(deep=True)
    raise KeyError(memory_id)


def get_source_turns(bundle: MemoryBundle, sources: list[SourceRef]) -> list[SourceTurn]:
    lookup = {turn.key: turn for turn in bundle.source_turns}
    keys = dict.fromkeys(source.key for source in sources)
    return [lookup[key].model_copy(deep=True) for key in keys]


def list_related_memories(
    bundle: MemoryBundle, *, entity: str, attribute: str, exclude_ids: list[str], limit: int = 20
) -> RelatedMemoryResult:
    if type(limit) is not int or limit <= 0:
        raise ValueError("limit must be a positive integer")
    if not entity.strip() or not attribute.strip():
        raise ValueError("entity and attribute must not be blank")
    order = {turn.key: i for i, turn in enumerate(bundle.source_turns)}
    excluded = set(exclude_ids)
    matches = [
        memory
        for memory in bundle.memories
        if memory.entity == entity
        and memory.attribute == attribute
        and memory.memory_id not in excluded
    ]
    matches.sort(key=lambda m: (order[m.sources[-1].key], m.memory_id))
    return RelatedMemoryResult(
        memories=[memory.model_copy(deep=True) for memory in matches[:limit]],
        truncated=len(matches) > limit,
    )
