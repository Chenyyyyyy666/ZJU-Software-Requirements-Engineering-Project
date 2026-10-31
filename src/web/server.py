"""Run the local Web UI: python -m src.web.server."""

import json
import os
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from dotenv import load_dotenv

from src.common.models import RetrievalRequest
from src.index.bm25 import IndexConfig, build_index, search_memories
from src.memory.store import get_memory, get_source_turns, load_memories
from src.reasoning.answer_generator import generate_answer
from src.reasoning.memory_selector import has_conflict, select_effective
from src.reasoning.query_analyzer import analyze_query
from src.reasoning.relation_judge import judge_relations

PROJECT_ROOT = Path(__file__).resolve().parents[2]
STATIC_DIR = Path(__file__).resolve().parent / "static"
MAX_REQUEST_BYTES = 64 * 1024


def memory_directory() -> Path:
    configured = Path(os.getenv("WEB_MEMORY_DIR", "outputs/memory"))
    return configured if configured.is_absolute() else PROJECT_ROOT / configured


def bundles_by_id() -> dict[str, Path]:
    """Load only the bundles beneath the configured memory output directory."""
    directory = memory_directory()
    bundles: dict[str, Path] = {}
    if not directory.is_dir():
        return bundles
    for path in sorted(directory.glob("*.json")):
        bundle = load_memories(path)
        bundles[bundle.conversation_id] = path
    return bundles


def bundle_for(conversation_id: str):
    path = bundles_by_id().get(conversation_id)
    if path is None:
        raise KeyError(f"Unknown conversation: {conversation_id}")
    return load_memories(path)


class WebHandler(BaseHTTPRequestHandler):
    server_version = "DynamicMemoryWeb/0.1"

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/":
                self._static("index.html", "text/html; charset=utf-8")
            elif parsed.path == "/app.css":
                self._static("app.css", "text/css; charset=utf-8")
            elif parsed.path == "/app.js":
                self._static("app.js", "text/javascript; charset=utf-8")
            elif parsed.path == "/api/conversations":
                self._conversations()
            elif parsed.path == "/api/memories":
                self._memories(parse_qs(parsed.query))
            elif parsed.path.startswith("/api/memories/"):
                memory_id = parsed.path.removeprefix("/api/memories/")
                self._memory_detail(memory_id, parse_qs(parsed.query))
            else:
                self._json(HTTPStatus.NOT_FOUND, {"error": "Not found"})
        except (ValueError, KeyError, OSError) as exc:
            self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
        except Exception:
            self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "Unable to load data"})

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        if urlparse(self.path).path != "/api/ask":
            self._json(HTTPStatus.NOT_FOUND, {"error": "Not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > MAX_REQUEST_BYTES:
                raise ValueError("Request body must be between 1 byte and 64 KiB")
            data = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(data, dict):
                raise ValueError("Request body must be a JSON object")
            conversation_id = data.get("conversation_id")
            question = data.get("question")
            if not isinstance(conversation_id, str) or not isinstance(question, str):
                raise ValueError("conversation_id and question must be strings")
            conversation_id = conversation_id.strip()
            question = question.strip()
            if not conversation_id or not question:
                raise ValueError("conversation_id and question are required")
            bundle = bundle_for(conversation_id)
            request = RetrievalRequest(
                conversation_id=conversation_id,
                query=question,
                top_k=int(data.get("top_k", os.getenv("RETRIEVAL_TOP_K", "10"))),
                candidate_k=max(30, int(data.get("top_k", os.getenv("RETRIEVAL_TOP_K", "10")))),
                mode="bm25",
            )
            retrieval = search_memories(build_index(bundle, config=IndexConfig(mode="bm25")), request)
            memories = [hit.memory for hit in retrieval.hits]
            analysis = analyze_query(question, conversation_id, retrieval.build_id)
            relations = judge_relations(memories)
            effective = select_effective(memories, relations, time_scope=analysis.time_scope)
            answer = generate_answer(
                question,
                effective,
                conflict=has_conflict(relations),
                build_id=retrieval.build_id,
            )
            self._json(
                HTTPStatus.OK,
                {
                    "build_id": retrieval.build_id,
                    "query_analysis": analysis.model_dump(mode="json"),
                    "retrieval": retrieval.model_dump(mode="json"),
                    "relations": [item.model_dump(mode="json") for item in relations],
                    "answer": answer.model_dump(mode="json"),
                    "memories": {item.memory_id: item.model_dump(mode="json") for item in memories},
                },
            )
        except (ValueError, KeyError, OSError, json.JSONDecodeError) as exc:
            self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
        except Exception:
            self._json(
                HTTPStatus.BAD_GATEWAY,
                {"error": "Question processing failed. Check the local model/API configuration."},
            )

    def _conversations(self) -> None:
        entries = []
        for conversation_id, path in bundles_by_id().items():
            bundle = load_memories(path)
            entries.append(
                {
                    "conversation_id": conversation_id,
                    "build_id": bundle.build_info.build_id,
                    "memory_count": len(bundle.memories),
                }
            )
        self._json(HTTPStatus.OK, {"conversations": entries, "memory_dir": str(memory_directory())})

    def _memories(self, query: dict[str, list[str]]) -> None:
        conversation_id = _query_value(query, "conversation_id")
        bundle = bundle_for(conversation_id)
        self._json(
            HTTPStatus.OK,
            {
                "conversation_id": bundle.conversation_id,
                "build_id": bundle.build_info.build_id,
                "memories": [item.model_dump(mode="json") for item in bundle.memories],
            },
        )

    def _memory_detail(self, memory_id: str, query: dict[str, list[str]]) -> None:
        conversation_id = _query_value(query, "conversation_id")
        bundle = bundle_for(conversation_id)
        memory = get_memory(bundle, memory_id)
        sources = get_source_turns(bundle, memory.sources)
        self._json(
            HTTPStatus.OK,
            {
                "build_id": bundle.build_info.build_id,
                "memory": memory.model_dump(mode="json"),
                "source_turns": [item.model_dump(mode="json") for item in sources],
            },
        )

    def _static(self, filename: str, content_type: str) -> None:
        path = STATIC_DIR / filename
        self._send(HTTPStatus.OK, path.read_bytes(), content_type)

    def _json(self, status: HTTPStatus, data: dict) -> None:
        body = json.dumps(data, ensure_ascii=False, allow_nan=False).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8")

    def _send(self, status: HTTPStatus, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args: object) -> None:
        print(f"[web] {self.address_string()} {fmt % args}")


def _query_value(query: dict[str, list[str]], name: str) -> str:
    values = query.get(name, [])
    if len(values) != 1 or not values[0].strip():
        raise ValueError(f"Exactly one non-empty {name} is required")
    return values[0].strip()


def main() -> None:
    load_dotenv()
    host = os.getenv("WEB_HOST", "127.0.0.1")
    port = int(os.getenv("WEB_PORT", "8000"))
    server = ThreadingHTTPServer((host, port), WebHandler)
    print(f"Dynamic Memory Web UI: http://{host}:{port}")
    print(f"Memory bundles: {memory_directory()}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping Web UI")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
