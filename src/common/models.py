"""JSON contracts shared by memory, retrieval, reasoning and Web modules."""

import re
from datetime import date, datetime
from typing import Annotated, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

NonEmpty = Annotated[str, StringConstraints(min_length=1, pattern=r"\S")]
PositiveInt = Annotated[int, Field(gt=0)]
FiniteFloat = Annotated[float, Field(allow_inf_nan=False)]
SCHEMA_VERSION = "0.1"


def validate_time(value: str | None) -> str | None:
    if value is not None:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            date.fromisoformat(value)
        elif re.match(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", value):
            datetime.fromisoformat(value)
        else:
            raise ValueError("Expected ISO date or datetime with seconds")
    return value


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, validate_assignment=True)


class SourceRef(Contract):
    session_id: PositiveInt
    turn_id: NonEmpty

    @property
    def key(self) -> tuple[int, str]:
        return self.session_id, self.turn_id


class SourceTurn(SourceRef):
    conversation_id: NonEmpty
    speaker: NonEmpty
    session_time: str | None
    text: NonEmpty

    _time = field_validator("session_time")(validate_time)


class Fact(Contract):
    """Only these fields may be returned by the extraction model."""

    content: NonEmpty
    entity: NonEmpty | None
    attribute: NonEmpty | None
    value: NonEmpty | None
    time_expression: NonEmpty | None
    event_time: str | None
    event_time_precision: Literal["day", "datetime", "unknown"]
    sources: Annotated[list[SourceRef], Field(min_length=1)]

    @model_validator(mode="after")
    def consistent_time(self) -> Self:
        if self.event_time_precision == "unknown":
            if self.event_time is not None:
                raise ValueError("unknown precision requires event_time=null")
        elif self.event_time is None:
            raise ValueError("Known precision requires event_time")
        else:
            validate_time(self.event_time)
            is_day = len(self.event_time) == 10
            if is_day != (self.event_time_precision == "day"):
                raise ValueError("event_time precision mismatch")
        if len({source.key for source in self.sources}) != len(self.sources):
            raise ValueError("Duplicate source reference")
        return self


class Memory(Fact):
    memory_id: NonEmpty
    conversation_id: NonEmpty
    session_time: str | None

    _time = field_validator("session_time")(validate_time)


class BuildInfo(Contract):
    build_id: NonEmpty
    method: Literal["llm_extraction", "turn_baseline", "manual_course_slide_example"]
    input_sha256: Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{64}$")] | None
    extractor_config_id: NonEmpty
    model: NonEmpty | None
    prompt_version: NonEmpty | None
    note: str | None

    @model_validator(mode="after")
    def require_provenance(self) -> Self:
        if self.method != "manual_course_slide_example" and self.input_sha256 is None:
            raise ValueError("Real builds require input_sha256")
        if self.method == "llm_extraction" and (self.model is None or self.prompt_version is None):
            raise ValueError("LLM builds require model and prompt_version")
        return self


class MemoryBundle(Contract):
    schema_version: Literal["0.1"]
    conversation_id: NonEmpty
    build_info: BuildInfo
    source_turns: list[SourceTurn]
    memories: list[Memory]

    @model_validator(mode="after")
    def validate_references(self) -> Self:
        sources = {turn.key: (i, turn) for i, turn in enumerate(self.source_turns)}
        if len(sources) != len(self.source_turns):
            raise ValueError("Duplicate source ID")
        if len({memory.memory_id for memory in self.memories}) != len(self.memories):
            raise ValueError("Duplicate memory_id")
        for item in [*self.source_turns, *self.memories]:
            if item.conversation_id != self.conversation_id:
                raise ValueError("Cross-conversation record")
        for memory in self.memories:
            keys = [source.key for source in memory.sources]
            if any(key not in sources for key in keys):
                raise ValueError(f"Missing source for {memory.memory_id}")
            if keys != sorted(keys, key=lambda key: sources[key][0]):
                raise ValueError(f"Unordered sources for {memory.memory_id}")
            if memory.session_time != sources[keys[-1]][1].session_time:
                raise ValueError(f"Session time does not match source: {memory.memory_id}")
        return self


class ExtractionResponse(Contract):
    memories: list[Fact]


class RetrievalRequest(Contract):
    conversation_id: NonEmpty
    query: NonEmpty
    top_k: PositiveInt = 10
    candidate_k: PositiveInt = 30
    mode: Literal["bm25", "vector", "hybrid"] = "bm25"
    entity: NonEmpty | None = None
    attribute: NonEmpty | None = None

    @model_validator(mode="after")
    def enough_candidates(self) -> Self:
        if self.candidate_k < self.top_k:
            raise ValueError("candidate_k must be >= top_k")
        return self


class ChannelScore(Contract):
    rank: PositiveInt
    score: FiniteFloat


class RetrievalHit(Contract):
    rank: PositiveInt
    memory: Memory
    score: FiniteFloat
    channel_scores: dict[str, ChannelScore]


class RetrievalResult(Contract):
    conversation_id: NonEmpty
    build_id: NonEmpty
    query: NonEmpty
    mode: Literal["bm25", "vector", "hybrid"]
    hits: list[RetrievalHit]
    trace: dict


class RelatedMemoryResult(Contract):
    memories: list[Memory]
    truncated: bool
