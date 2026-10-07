"""Read only raw conversation fields from official LoCoMo records."""

import json
import re
import warnings
from datetime import datetime
from pathlib import Path

from src.common.models import SourceTurn, validate_time

MONTHS = {
    name: i + 1
    for i, name in enumerate(
        [
            "january",
            "february",
            "march",
            "april",
            "may",
            "june",
            "july",
            "august",
            "september",
            "october",
            "november",
            "december",
        ]
    )
}
IMAGE_URL = re.compile(
    r"https?://[^\s<>\"')]+\.(?:jpe?g|png|gif|webp|bmp|svg)(?:\?[^\s<>\"')]+)?", re.I
)


def normalize_session_time(value: object) -> str | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise ValueError("Session time must be a string or null")
    value = value.strip()
    try:
        return validate_time(value)
    except ValueError:
        pass
    match = re.fullmatch(
        r"(\d{1,2}):(\d{2})\s*(am|pm)\s+on\s+(\d{1,2})\s+([A-Za-z]+),?\s+(\d{4})",
        value,
        re.I,
    )
    if match:
        hour, minute, period, day, month, year = match.groups()
        if 1 <= int(hour) <= 12 and month.lower() in MONTHS:
            try:
                hour24 = int(hour) % 12 + (12 if period.lower() == "pm" else 0)
                return datetime(
                    int(year), MONTHS[month.lower()], int(day), hour24, int(minute)
                ).isoformat()
            except ValueError:
                pass
    warnings.warn("Unrecognized session timestamp; keeping null", stacklevel=2)
    return None


def clean_text(text: str, image_urls: object = None) -> str:
    # Only known image URLs and image Markdown are removed; ordinary text URLs stay.
    if isinstance(image_urls, str):
        image_urls = [image_urls]
    if isinstance(image_urls, list):
        for url in image_urls:
            if isinstance(url, str) and url:
                text = text.replace(url, "")
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text)
    return IMAGE_URL.sub("", text).strip()


def load_conversation(data_path: Path, conversation_id: str) -> list[SourceTurn]:
    records = json.loads(data_path.read_text(encoding="utf-8"))
    if not isinstance(records, list):
        raise ValueError("LoCoMo root must be a list of sample_id/conversation records")
    if any(not isinstance(record, dict) for record in records):
        raise ValueError("Each LoCoMo record must be an object")
    matches = [record for record in records if record.get("sample_id") == conversation_id]
    if not matches:
        raise KeyError(f"Unknown conversation: {conversation_id}")
    if len(matches) != 1:
        raise ValueError(f"Duplicate sample_id: {conversation_id}")
    conversation = matches[0].get("conversation")
    if not isinstance(conversation, dict):
        raise ValueError("conversation must be an object")
    sessions = sorted(
        (int(key.removeprefix("session_")), key)
        for key in conversation
        if re.fullmatch(r"session_[1-9]\d*", key)
    )
    turns = []
    seen = set()
    for session_id, session_key in sessions:
        session = conversation[session_key]
        if not isinstance(session, list):
            raise ValueError(f"{session_key} must be a list")
        timestamp = normalize_session_time(conversation.get(f"{session_key}_date_time"))
        for raw in session:
            if not isinstance(raw, dict):
                raise ValueError("Each turn must be an object")
            # Validate IDs even for image-only turns so filtering cannot hide duplicates.
            turn_id = raw.get("dia_id")
            if not isinstance(turn_id, str) or not turn_id.strip():
                raise ValueError("Missing dia_id")
            key = (session_id, turn_id)
            if key in seen:
                raise ValueError(f"Duplicate source: {key}")
            seen.add(key)
            text = raw.get("text")
            if not isinstance(text, str):
                raise ValueError(f"Missing text: {key}")
            text = clean_text(text, raw.get("img_url"))
            if not text:
                continue
            turns.append(
                SourceTurn(
                    conversation_id=conversation_id,
                    session_id=session_id,
                    turn_id=turn_id,
                    speaker=raw.get("speaker"),
                    session_time=timestamp,
                    text=text,
                )
            )
    return turns
