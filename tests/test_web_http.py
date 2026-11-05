"""Exercise local HTTP routes with real sockets and mocked model responses.

These tests prove HTTP behavior, not browser rendering or model quality. All
model entry points are blocked by default, so no external service is called.
"""

from __future__ import annotations

import http.client
import json
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from src.common.models import AnswerRecord, MemoryRelation, QueryAnalysisResult
from src.web import server as web


@pytest.fixture
def http_server(tmp_path, monkeypatch):
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()
    demo_path = Path(__file__).resolve().parents[1] / "examples/demo_dynamic_memory.json"
    bundle_data = json.loads(demo_path.read_text(encoding="utf-8"))
    (memory_dir / "demo.json").write_text(json.dumps(bundle_data), encoding="utf-8")
    monkeypatch.setenv("WEB_MEMORY_DIR", str(memory_dir))

    def blocked_model(*args, **kwargs):
        raise AssertionError("External model calls are forbidden in HTTP tests")

    monkeypatch.setattr(web, "analyze_query", blocked_model)
    monkeypatch.setattr(web, "judge_relations", blocked_model)
    monkeypatch.setattr(web, "generate_answer", blocked_model)
    monkeypatch.setattr(web.WebHandler, "log_message", lambda *args: None)
    try:
        server = ThreadingHTTPServer(("127.0.0.1", 0), web.WebHandler)
    except PermissionError as exc:
        pytest.skip(f"sandbox does not permit loopback sockets: {exc}")
    thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.01), daemon=True)
    thread.start()
    yield server, bundle_data
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)
    assert not thread.is_alive()


def request(server, method, path, *, payload=None, raw=None, headers=None):
    connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
    body = json.dumps(payload).encode("utf-8") if payload is not None else raw
    request_headers = {"Content-Type": "application/json", **(headers or {})}
    try:
        connection.request(method, path, body=body, headers=request_headers)
        response = connection.getresponse()
        data = response.read()
        return response.status, dict(response.headers), data
    finally:
        connection.close()


def json_request(*args, **kwargs):
    status, headers, raw = request(*args, **kwargs)
    assert headers["Content-Type"] == "application/json; charset=utf-8"
    assert int(headers["Content-Length"]) == len(raw)
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert headers["Cache-Control"] == "no-store"
    return status, json.loads(raw)


def test_http_lists_real_bundle_and_returns_original_source(http_server):
    server, bundle = http_server
    status, data = json_request(server, "GET", "/api/conversations")
    assert status == 200
    assert data["conversations"] == [
        {
            "conversation_id": "demo-state-change",
            "build_id": bundle["build_info"]["build_id"],
            "memory_count": 2,
        }
    ]
    status, data = json_request(server, "GET", "/api/memories?conversation_id=demo-state-change")
    assert status == 200 and len(data["memories"]) == 2
    memory = bundle["memories"][0]
    status, detail = json_request(
        server,
        "GET",
        f"/api/memories/{memory['memory_id']}?conversation_id=demo-state-change",
    )
    assert status == 200
    assert detail["memory"] == memory
    assert detail["source_turns"] == [bundle["source_turns"][0]]
    assert detail["build_id"] == bundle["build_info"]["build_id"]


@pytest.mark.parametrize(
    "path",
    [
        "/api/memories",
        "/api/memories?conversation_id=",
        "/api/memories?conversation_id=a&conversation_id=b",
        "/api/memories?conversation_id=unknown",
        "/api/memories/unknown?conversation_id=demo-state-change",
    ],
)
def test_http_rejects_missing_duplicate_or_unknown_selection(http_server, path):
    server, _ = http_server
    status, data = json_request(server, "GET", path)
    assert status == 400
    assert data["error"]


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"conversation_id": "demo-state-change"},
        {"conversation_id": "demo-state-change", "question": "  "},
        {"conversation_id": "unknown", "question": "Where?"},
        {"conversation_id": "demo-state-change", "question": ["invalid type"]},
        {"conversation_id": "demo-state-change", "question": "adoption", "top_k": 0},
        {"conversation_id": "demo-state-change", "question": "adoption", "top_k": "invalid"},
        ["not an object"],
    ],
)
def test_http_rejects_invalid_ask_requests_without_model_calls(http_server, payload):
    server, _ = http_server
    status, data = json_request(server, "POST", "/api/ask", payload=payload)
    assert status == 400
    assert data["error"]


@pytest.mark.parametrize("body", [b"not-json", b"{", b"", b"\xff"])
def test_http_rejects_bad_json_and_empty_or_invalid_utf8_body(http_server, body):
    server, _ = http_server
    status, data = json_request(server, "POST", "/api/ask", raw=body)
    assert status == 400
    assert data["error"]


def test_http_rejects_oversized_body_before_processing(http_server):
    server, _ = http_server
    status, data = json_request(server, "POST", "/api/ask", raw=b"x" * (web.MAX_REQUEST_BYTES + 1))
    assert status == 400
    assert "64 KiB" in data["error"]


@pytest.mark.parametrize("relation_type", ["RETAIN", "UPDATE", "SUPERSEDE", "CONFLICT"])
def test_http_serializes_each_relation_and_valid_memory_selection(
    http_server, monkeypatch, relation_type
):
    server, bundle = http_server
    build_id = bundle["build_info"]["build_id"]
    memory_ids = [memory["memory_id"] for memory in bundle["memories"]]
    expected_ids = memory_ids[1:] if relation_type == "SUPERSEDE" else memory_ids

    def analyze(question, conversation, version):
        assert question == "What is Alex's adoption status?"
        assert conversation == "demo-state-change" and version == build_id
        return QueryAnalysisResult(
            conversation_id=conversation,
            build_id=version,
            time_scope="current",
            confidence=1.0,
        )

    def judge(memories):
        assert {memory.memory_id for memory in memories} == set(memory_ids)
        return [
            MemoryRelation(
                memory_a=memory_ids[0],
                memory_b=memory_ids[1],
                relation_type=relation_type,
                reason="Mocked HTTP contract response; not a model result",
                sources=[source for memory in memories for source in memory.sources],
            )
        ]

    def generate(question, effective, *, conflict, build_id):
        assert {memory.memory_id for memory in effective} == set(expected_ids)
        assert conflict == (relation_type == "CONFLICT")
        return AnswerRecord(
            answer="Mock answer for HTTP validation.",
            valid_memory_ids=[memory.memory_id for memory in effective],
            conflict=conflict,
            build_id=build_id,
        )

    monkeypatch.setattr(web, "analyze_query", analyze)
    monkeypatch.setattr(web, "judge_relations", judge)
    monkeypatch.setattr(web, "generate_answer", generate)
    status, data = json_request(
        server,
        "POST",
        "/api/ask",
        payload={
            "conversation_id": "demo-state-change",
            "question": "What is Alex's adoption status?",
        },
    )
    assert status == 200
    assert data["relations"][0]["relation_type"] == relation_type
    assert data["answer"]["conflict"] == (relation_type == "CONFLICT")
    assert set(data["answer"]["valid_memory_ids"]) == set(expected_ids)
    assert data["build_id"] == data["answer"]["build_id"] == build_id
    assert set(data["memories"]) == set(memory_ids)


def test_http_model_failure_is_sanitized_gateway_error(http_server, monkeypatch):
    server, _ = http_server

    def model_failure(*args):
        raise RuntimeError("PRIVATE_SERVICE_RESPONSE_MUST_NOT_BE_EXPOSED")

    monkeypatch.setattr(web, "analyze_query", model_failure)
    status, data = json_request(
        server,
        "POST",
        "/api/ask",
        payload={"conversation_id": "demo-state-change", "question": "adoption status"},
    )
    assert status == 502
    assert "PRIVATE_SERVICE_RESPONSE" not in data["error"]


def test_http_serves_static_files_and_unknown_routes(http_server):
    server, _ = http_server
    for path, content_type in (
        ("/", "text/html"),
        ("/app.js", "text/javascript"),
        ("/app.css", "text/css"),
    ):
        status, headers, body = request(server, "GET", path)
        assert status == 200 and body
        assert headers["Content-Type"].startswith(content_type)
    for method in ("GET", "POST"):
        status, data = json_request(server, method, "/unknown-route")
        assert status == 404 and data["error"] == "Not found"


def test_http_empty_memory_directory_is_clear(http_server, monkeypatch, tmp_path):
    server, _ = http_server
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setenv("WEB_MEMORY_DIR", str(empty))
    status, data = json_request(server, "GET", "/api/conversations")
    assert status == 200 and data["conversations"] == []
