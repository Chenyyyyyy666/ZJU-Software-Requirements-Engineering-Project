"""Export a minimal, label-free answer file for one course conversation."""

import argparse
import json
from pathlib import Path

from src.common.json_io import write_json


def export_answers(input_path: Path, output_path: Path, conversation: str) -> int:
    payload = json.loads(input_path.read_text(encoding="utf-8-sig"))
    rows = payload.get("answers", []) if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        raise ValueError("input must contain answer rows")
    result, ids = [], set()
    for row in rows:
        if row.get("conversation_id") != conversation:
            continue
        identifier, answer = row.get("question_id"), row.get("answer")
        if not identifier or identifier in ids or not isinstance(answer, str) or not answer.strip():
            raise ValueError("duplicate/missing question id or empty answer")
        ids.add(identifier)
        result.append({"question_id": identifier, "answer": answer.strip()})
    if not result:
        raise ValueError("no answer rows for the selected conversation")
    write_json(result, output_path)
    return len(result)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--conversation", required=True)
    args = parser.parse_args()
    count = export_answers(args.input, args.output, args.conversation)
    print(f"Exported {count} label-free answer rows to {args.output}")


if __name__ == "__main__":
    main()
