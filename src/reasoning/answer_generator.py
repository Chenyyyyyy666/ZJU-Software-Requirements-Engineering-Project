"""Generate the final answer from effective memories."""

import os

from openai import OpenAI

from src.common.models import AnswerRecord, Memory
from src.reasoning.telemetry import tracked_completion

PROMPT_VERSION = "answer_v1"

SYSTEM_PROMPT = """Answer the question using only the memories below.
If a conflict is marked, mention both possibilities.
Output one sentence.Each memory shows the date it was said on. Resolve relative expressions such as
"yesterday" or "last weekend" against that date, and answer with a full date when asked "when"."""


def _client() -> OpenAI:
    return OpenAI(
        max_retries=0,
        timeout=60,
        api_key=os.environ["DEEPSEEK_API_KEY"],
        base_url=os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
    )


def generate_answer(
    question: str,
    effective: list[Memory],
    *,
    conflict: bool,
    build_id: str,
    question_id: str | None = None,
) -> AnswerRecord:
    lines = [
        f"- (said on {m.session_time}; event time: {m.event_time or 'unknown'}) {m.content}"
        for m in effective
    ]
    if conflict:
        lines.append("[Note: conflicting information exists.]")
    user = "Memories:\n" + "\n".join(lines) + f"\n\nQuestion: {question}"

    resp = tracked_completion(
        _client(),
        model=os.environ.get("DEEPSEEK_MODEL", "deepseek-flash"),
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user},
        ],
        extra_body={"thinking": {"type": "disabled"}},
        temperature=0,
        max_tokens=256,
    )
    text = (resp.choices[0].message.content or "").strip()
    return AnswerRecord(
        question_id=question_id,
        answer=text or "(no answer)",
        valid_memory_ids=[m.memory_id for m in effective],
        conflict=conflict,
        build_id=build_id,
    )
