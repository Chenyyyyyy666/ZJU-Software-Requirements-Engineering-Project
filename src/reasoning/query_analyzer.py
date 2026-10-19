import json
import os

import openai
from dotenv import load_dotenv
from openai import OpenAI
from pydantic import ValidationError

from src.common.models import QueryAnalysisResult

load_dotenv()

PROMPT_VERSION = "query_v1"

SYSTEM_PROMPT = """You analyze a user question about a long conversation.
Output JSON only with these fields:
- entity: list of entity names mentioned (may be empty)
- time_scope: "current", "past", or "specific"
- time_expression: time phrase from the question, or null
- attribute: attribute or event category, or null
- confidence: number between 0 and 1

Example input: "Where does Caroline live now?"
Example JSON output: {"entity": ["Caroline"], "time_scope": "current",
"time_expression": null, "attribute": "residence", "confidence": 0.9}"""


def _client() -> OpenAI:
    return OpenAI(
        api_key=os.environ["DEEPSEEK_API_KEY"],
        base_url=os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
    )


def analyze_query(
    question: str, conversation_id: str, build_id: str, *, max_attempts: int = 2
) -> QueryAnalysisResult:
    last_error: Exception | None = None
    client = _client()
    for _ in range(max_attempts):
        try:
            resp = client.chat.completions.create(
                model=os.environ.get("DEEPSEEK_MODEL", "deepseek-flash"),
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": question},
                ],
                response_format={"type": "json_object"},
                extra_body={"thinking": {"type": "disabled"}},
                max_tokens=512,
            )
        except openai.AuthenticationError:
            raise
        except openai.APIStatusError as exc:
            if exc.status_code in (401, 402):
                raise
            last_error = exc
            continue
        except openai.APIConnectionError as exc:
            last_error = exc
            continue

        raw = resp.choices[0].message.content
        if not raw:
            last_error = ValueError("empty content")
            continue
        try:
            data = json.loads(raw)
            return QueryAnalysisResult(
                conversation_id=conversation_id,
                build_id=build_id,
                entity=data.get("entity") or [],
                time_scope=data["time_scope"],
                time_expression=data.get("time_expression"),
                attribute=data.get("attribute"),
                confidence=data["confidence"],
            )
        except (json.JSONDecodeError, KeyError, ValidationError) as exc:
            last_error = exc
    raise ValueError(f"Query analysis failed after {max_attempts} attempts") from last_error