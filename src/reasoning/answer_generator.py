"""Generate the final answer from effective memories."""

import os

from openai import OpenAI

from src.common.models import AnswerRecord, Memory
from src.reasoning.telemetry import tracked_completion

PROMPT_VERSION = "answer_v2"

SYSTEM_PROMPT = """Answer using only the memories below.
Return only the shortest exact answer phrase. Do not explain, cite memories, restate the
question, add background, or write a full sentence unless the answer is Yes or No.
- "When" questions: return the date or time phrase only.
- "Who", "what", "which" and "where" questions: return the name or value only.
- "How many" and "how often" questions: return the quantity only.
- Yes/no questions: return only "Yes" or "No".
Each memory shows when it was said. Resolve relative expressions against that date.
When facts conflict, use the latest event time, then the latest session time; do not
describe the conflict in the answer. If the memories cannot answer, return exactly
"Not enough information."
"""


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
