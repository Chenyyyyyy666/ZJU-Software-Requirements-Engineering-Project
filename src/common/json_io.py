"""Canonical hashing and atomic UTF-8 JSON writes."""

import hashlib
import json
from pathlib import Path
from tempfile import NamedTemporaryFile


def digest(value: object) -> str:
    text = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def write_json(value: object, path: Path) -> None:
    text = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = None
    try:
        with NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="\n", dir=path.parent, delete=False
        ) as handle:
            temp_path = Path(handle.name)
            handle.write(text)
        temp_path.replace(path)
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
