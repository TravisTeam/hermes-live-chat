import json
import time
from dataclasses import replace
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from voice_agent import server
from voice_agent.hermes_client import VoiceProfile


def test_typed_stream_keeps_browser_session(monkeypatch):
    captured = {}

    def fake_turn(text, profile_id, session_id):
        captured.update(text=text, profile_id=profile_id, session_id=session_id)
        return "First sentence. Second sentence!", VoiceProfile(
            "hermes_current", "Active model", "default", "test"
        )

    monkeypatch.setattr(server, "run_hermes_turn", fake_turn)
    response = TestClient(server.app).post(
        "/api/chat-stream",
        json={
            "message": "Remember number seven.",
            "session_id": "browser-123",
            "profile_id": "hermes_current",
            "speak": False,
        },
    )

    assert response.status_code == 200
    events = [json.loads(line) for line in response.text.splitlines()]
    assert events[0]["type"] == "user"
    assert events[-1]["type"] == "done"
    assert "".join(e["text"] for e in events if e["type"] == "assistant_delta") == (
        "First sentence. Second sentence!"
    )
    assert captured["session_id"] == "browser-123"


def test_empty_typed_message_is_rejected():
    response = TestClient(server.app).post(
        "/api/chat-stream", json={"message": "   ", "speak": False}
    )
    assert response.status_code == 422


def test_acknowledgement_matches_task_type():
    assert "look into" in server.acknowledgement_for("Please research the roof issue")
    assert "work on" in server.acknowledgement_for("Build a new report")
    assert server.acknowledgement_for("Tell me something") == "Got it. I'm on it."


def test_long_turn_emits_progress_until_result(monkeypatch, tmp_path):
    settings = replace(server.get_settings(), hermes_progress_interval_seconds=0.01)

    def slow_turn(text, profile_id, session_id):
        time.sleep(0.12)
        return "Long task complete.", VoiceProfile(
            "hermes_current", "Active model", "default", "test"
        )

    monkeypatch.setattr(server, "get_settings", lambda: settings)
    monkeypatch.setattr(server, "artifact_session_dir", lambda session_id: tmp_path)
    monkeypatch.setattr(server, "run_hermes_turn", slow_turn)
    events = [
        json.loads(line)
        for line in server.reply_events(
            "Do a long task", "browser-123", "hermes_current", False
        )
    ]

    progress = [event for event in events if event["type"] == "progress"]
    assert progress
    assert "still" in progress[0]["text"].lower()
    assert next(event for event in events if event["type"] == "assistant_done")["text"] == "Long task complete."


def test_new_hermes_file_is_streamed_as_an_artifact(monkeypatch, tmp_path):
    session_dir = tmp_path / "voice-web-browser-123"
    session_dir.mkdir()

    def fake_turn(text, profile_id, session_id):
        (session_dir / "inspection notes.txt").write_text("Ready for download.")
        return "I created the notes file.", VoiceProfile(
            "hermes_current", "Active model", "default", "test"
        )

    monkeypatch.setattr(server, "artifact_session_dir", lambda session_id: session_dir)
    monkeypatch.setattr(server, "run_hermes_turn", fake_turn)
    events = [
        json.loads(line)
        for line in server.reply_events(
            "Create notes", "browser-123", "hermes_current", False
        )
    ]
    artifact = next(event for event in events if event["type"] == "artifact")
    assert artifact["name"] == "inspection notes.txt"
    assert artifact["kind"] == "file"
    assert "inspection%20notes.txt?v=" in artifact["url"]


def test_artifact_download_is_sandboxed(monkeypatch, tmp_path):
    session_dir = tmp_path / "voice-web-browser-123"
    session_dir.mkdir()
    (session_dir / "result.html").write_text("<script>alert(1)</script>")
    monkeypatch.setattr(
        server, "get_settings", lambda: SimpleNamespace(artifact_dir=str(tmp_path))
    )
    response = TestClient(server.app).get(
        "/artifacts/voice-web-browser-123/result.html"
    )
    assert response.status_code == 200
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["content-security-policy"].startswith("sandbox")
    blocked = TestClient(server.app).get(
        "/artifacts/voice-web-browser-123/../outside.txt"
    )
    assert blocked.status_code == 404


def test_prometheus_metric_parser_sums_matching_series():
    metrics = """
# HELP vllm:generation_tokens_total generated tokens
vllm:generation_tokens_total{engine="0"} 120.0
vllm:generation_tokens_total{engine="1"} 80.0
vllm:prompt_tokens_total{engine="0"} 500.0
"""
    assert server._prometheus_value(metrics, "vllm:generation_tokens_total") == 200.0
    assert server._prometheus_value(metrics, "vllm:prompt_tokens_total") == 500.0
    assert server._prometheus_value(metrics, "vllm:missing") == 0.0


def test_llm_metrics_endpoint(monkeypatch):
    monkeypatch.setattr(
        server,
        "current_llm_metrics",
        lambda: {
            "generation_tokens": 50.0,
            "prompt_tokens": 1000.0,
            "prompt_requests": 1.0,
            "decode_seconds": 2.0,
            "context_limit": 1048576,
        },
    )
    response = TestClient(server.app).get("/api/llm-metrics")
    assert response.status_code == 200
    assert response.json()["context_limit"] == 1048576


@pytest.mark.parametrize(
    ("filename", "expected_kind"),
    [
        ("screenshot.png", "image"),
        ("photo.jpg", "image"),
        ("animation.gif", "image"),
        ("picture.webp", "image"),
        ("clip.mp3", "audio"),
        ("recording.wav", "audio"),
        ("voice.m4a", "audio"),
        ("movie.mp4", "video"),
        ("demo.webm", "video"),
        ("report.pdf", "file"),
        ("drawing.svg", "file"),
        ("bundle.zip", "file"),
    ],
)
def test_artifact_media_classification(tmp_path, filename, expected_kind):
    (tmp_path / filename).write_bytes(b"test")
    event = server.artifact_events(tmp_path, {})[0]
    assert event["kind"] == expected_kind
    assert "?v=" in event["url"]


def test_session_artifact_listing(monkeypatch, tmp_path):
    session_dir = tmp_path / "voice-web-phone"
    session_dir.mkdir()
    (session_dir / "mobile screenshot.png").write_bytes(b"png")
    monkeypatch.setattr(server, "artifact_session_dir", lambda session_id: session_dir)
    response = TestClient(server.app).get("/api/artifacts/phone")
    assert response.status_code == 200
    assert response.json()[0]["name"] == "mobile screenshot.png"
    assert "mobile%20screenshot.png" in response.json()[0]["url"]
