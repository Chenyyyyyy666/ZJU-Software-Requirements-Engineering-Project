"""Generate the final answer from effective memories."""

import os

from openai import OpenAI

from src.common.models import AnswerRecord, Memory

PROMPT_VERSION = "answer_v1"

SYSTEM_PROMPT = """Answer the question using only the memories below.
If a conflict is marked, mention both possibilities.
Output one sentence."""


def _client() -> OpenAI:
    return OpenAI(
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
    lines = [f"- {m.content}" for m in effective]
    if conflict:
        lines.append("[Note: conflicting information exists.]")
    user = "Memories:\n" + "\n".join(lines) + f"\n\nQuestion: {question}"

    resp = _client().chat.completions.create(
        model=os.environ.get("DEEPSEEK_MODEL", "deepseek-flash"),
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user},
        ],
        extra_body={"thinking": {"type": "disabled"}},
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