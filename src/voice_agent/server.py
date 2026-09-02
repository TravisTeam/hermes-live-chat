from __future__ import annotations

import json
import mimetypes
import os
import re
import shutil
import subprocess
import time
import uuid
from collections.abc import Iterator
from functools import lru_cache
from pathlib import Path
from urllib.parse import quote

import requests
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .audio import convert_to_wav
from .config import get_settings
from .hermes_client import artifact_session_dir, cancel_hermes_turn, load_voice_profiles, run_hermes_turn
from .stt import transcribe_wav
from .tts import KokoroClient, audio_data_uri, split_spoken_chunks

mimetypes.add_type("image/webp", ".webp")

app = FastAPI(title="Hermes Live Chat", version="1.0.0")
static_dir = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=static_dir), name="static")


class ChatRequest(BaseModel):
    message: str
    session_id: str = "default"
    profile_id: str = "hermes_current"
    speak: bool = True


class TTSRequest(BaseModel):
    text: str


@app.get("/")
def index() -> FileResponse:
    return FileResponse(static_dir / "index.html")


def _reachable(url: str, timeout: float = 2.0) -> bool:
    try:
        return requests.get(url, timeout=timeout).status_code < 500
    except requests.RequestException:
        return False


def _prometheus_value(metrics: str, name: str) -> float:
    total = 0.0
    found = False
    for line in metrics.splitlines():
        if line.startswith(name + " ") or line.startswith(name + "{"):
            try:
                total += float(line.rsplit(" ", 1)[-1])
                found = True
            except ValueError:
                continue
    return total if found else 0.0


@lru_cache(maxsize=1)
def llm_model_info() -> dict[str, str | int]:
    try:
        base = get_settings().model_server_url.rstrip("/")
        model = requests.get(base + "/v1/models", timeout=2).json()["data"][0]
        return {"name": str(model.get("id", "Active model")), "context_limit": int(model.get("max_model_len", 0))}
    except (requests.RequestException, KeyError, IndexError, TypeError, ValueError):
        return {"name": "Active model", "context_limit": 0}


def llm_context_limit() -> int:
    return int(llm_model_info()["context_limit"])


def current_llm_metrics() -> dict[str, float | int]:
    try:
        text = requests.get(get_settings().model_server_url.rstrip("/") + "/metrics", timeout=2).text
        return {
            "generation_tokens": _prometheus_value(text, "vllm:generation_tokens_total"),
            "prompt_tokens": _prometheus_value(text, "vllm:prompt_tokens_total"),
            "prompt_requests": _prometheus_value(text, "vllm:request_prompt_tokens_count"),
            "decode_seconds": _prometheus_value(text, "vllm:request_decode_time_seconds_sum"),
            "context_limit": llm_context_limit(),
        }
    except requests.RequestException:
        return {"generation_tokens": 0.0, "prompt_tokens": 0.0, "prompt_requests": 0.0, "decode_seconds": 0.0, "context_limit": llm_context_limit()}


@app.get("/api/health")
def health() -> dict[str, object]:
    settings = get_settings()
    hermes_ok = shutil.which("hermes") is not None
    kokoro_ok = _reachable(settings.kokoro_url.rstrip("/") + "/api/health")
    llm_ok = _reachable(settings.model_server_url.rstrip("/") + "/v1/models")
    canary_ok = Path(settings.canary_binary).is_file() and Path(settings.canary_model).is_file()
    return {
        "status": "ok" if hermes_ok and llm_ok else "degraded",
        "hermes": hermes_ok,
        "llm": llm_ok,
        "model_name": llm_model_info()["name"],
        "whisper": canary_ok if settings.stt_engine == "canary" else True,
        "stt_engine": "Canary 180M Flash Q8_0" if settings.stt_engine == "canary" else f"Whisper {settings.whisper_model}",
        "kokoro": kokoro_ok,
    }


@app.get("/api/llm-metrics")
def llm_metrics() -> dict[str, float | int]:
    return current_llm_metrics()


@app.get("/api/config")
def config() -> dict[str, object]:
    s = get_settings()
    return {
        "mode": "hermes",
        "model": llm_model_info()["name"],
        "model_server_url": s.model_server_url,
        "kokoro_url": s.kokoro_url,
        "kokoro_voice": s.kokoro_voice,
        "kokoro_speed": s.kokoro_speed,
        "stt_engine": s.stt_engine,
        "stt_model": "Canary 180M Flash Q8_0 (208 MB)" if s.stt_engine == "canary" else s.whisper_model,
        "whisper_fallback": s.whisper_model,
        "profiles": profiles(),
    }


@app.get("/api/profiles")
def profiles() -> list[dict[str, str]]:
    return [
        {
            "id": p.id,
            "label": p.label,
            "hermes_profile": p.hermes_profile,
            "description": p.description,
        }
        for p in load_voice_profiles()
    ]


def log_event(message: str, *, turn_id: str, started_at: float, **data: object) -> None:
    print(json.dumps({
        "time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "message": message,
        "turn_id": turn_id,
        "elapsed_ms": int((time.monotonic() - started_at) * 1000),
        **data,
    }), flush=True)


def acknowledgement_for(message: str) -> str:
    lowered = message.lower()
    if any(word in lowered for word in ("look up", "find", "check", "search", "research", "investigate")):
        return "No problem. Let me look into that for you."
    if any(word in lowered for word in ("build", "create", "make", "fix", "change", "update", "analyze", "work on")):
        return "Absolutely. I'll work on that now."
    return "Got it. I'm on it."


def snapshot_artifacts(directory: Path) -> dict[str, tuple[int, int]]:
    snapshot: dict[str, tuple[int, int]] = {}
    for path in directory.rglob("*"):
        if not path.is_file() or path.is_symlink():
            continue
        try:
            stat = path.stat()
            snapshot[path.relative_to(directory).as_posix()] = (stat.st_mtime_ns, stat.st_size)
        except OSError:
            continue
    return snapshot


def artifact_events(
    directory: Path, before: dict[str, tuple[int, int]]
) -> list[dict[str, object]]:
    events: list[dict[str, object]] = []
    root = directory.resolve()
    for relative, metadata in snapshot_artifacts(directory).items():
        if before.get(relative) == metadata:
            continue
        path = (directory / relative).resolve()
        if not path.is_relative_to(root):
            continue
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        if mime.startswith("image/") and mime != "image/svg+xml":
            kind = "image"
        elif mime.startswith("audio/"):
            kind = "audio"
        elif mime.startswith("video/"):
            kind = "video"
        else:
            kind = "file"
        encoded_path = "/".join(quote(part, safe="") for part in Path(relative).parts)
        events.append({
            "type": "artifact",
            "name": path.name,
            "url": f"artifacts/{quote(directory.name, safe='')}/{encoded_path}?v={metadata[0]}",
            "mime": mime,
            "kind": kind,
            "size": metadata[1],
        })
    return sorted(events, key=lambda event: str(event["name"]).lower())


@app.get("/api/artifacts/{session_id}")
def list_artifacts(session_id: str) -> list[dict[str, object]]:
    return artifact_events(artifact_session_dir(session_id), {})


def transcribe_upload(audio: UploadFile) -> tuple[str, str, float]:
    settings = get_settings()
    temp_dir = Path(settings.temp_dir)
    temp_dir.mkdir(parents=True, exist_ok=True)
    turn_id = uuid.uuid4().hex
    started_at = time.monotonic()
    raw_path = temp_dir / f"{turn_id}-{audio.filename or 'audio.webm'}"
    wav_path = temp_dir / f"{turn_id}.wav"
    try:
        log_event("stt_received", turn_id=turn_id, started_at=started_at)
        with raw_path.open("wb") as handle:
            shutil.copyfileobj(audio.file, handle)
        log_event("audio_received", turn_id=turn_id, started_at=started_at, bytes=raw_path.stat().st_size)
        convert_to_wav(raw_path, wav_path)
        transcript = transcribe_wav(wav_path, settings=settings)
        if not transcript:
            raise HTTPException(status_code=422, detail="No speech detected")
        log_event("stt_complete", turn_id=turn_id, started_at=started_at, chars=len(transcript))
        return transcript, turn_id, started_at
    finally:
        raw_path.unlink(missing_ok=True)
        wav_path.unlink(missing_ok=True)


@app.post("/api/transcribe")
def transcribe(audio: UploadFile = File(...)) -> dict[str, str]:
    transcript, turn_id, _ = transcribe_upload(audio)
    return {"transcript": transcript, "turn_id": turn_id}


def reply_events(
    message: str,
    session_id: str,
    profile_id: str,
    speak: bool,
    turn_id: str | None = None,
    started_at: float | None = None,
) -> Iterator[str]:
    settings = get_settings()
    turn_id = turn_id or uuid.uuid4().hex
    started_at = started_at or time.monotonic()
    turn_artifact_dir = artifact_session_dir(session_id)
    artifacts_before = snapshot_artifacts(turn_artifact_dir)
    yield json.dumps({"type": "user", "text": message, "turn_id": turn_id}) + "\n"
    try:
        if speak:
            acknowledgement = acknowledgement_for(message)
            log_event("ack_start", turn_id=turn_id, started_at=started_at, text=acknowledgement)
            ack_audio = KokoroClient(settings).synthesize(acknowledgement)
            log_event("ack_ready", turn_id=turn_id, started_at=started_at, bytes=len(ack_audio))
            yield json.dumps({
                "type": "ack",
                "text": acknowledgement,
                "audio": audio_data_uri(ack_audio),
            }) + "\n"
        log_event("hermes_start", turn_id=turn_id, started_at=started_at, session_id=session_id)
        reply, selected_profile = run_hermes_turn(
            message, profile_id=profile_id, session_id=session_id
        )
        log_event("hermes_complete", turn_id=turn_id, started_at=started_at, chars=len(reply))
        yield json.dumps({"type": "assistant_start", "profile_id": selected_profile.id}) + "\n"
        # Hermes's programmatic CLI returns the completed turn. Emit small text deltas so
        # the browser remains responsive, then synthesize natural sentence chunks.
        words = reply.split()
        for index in range(0, len(words), 8):
            text = " ".join(words[index:index + 8])
            if index + 8 < len(words):
                text += " "
            yield json.dumps({"type": "assistant_delta", "text": text}) + "\n"
        yield json.dumps({"type": "assistant_done", "text": reply}) + "\n"
        for artifact in artifact_events(turn_artifact_dir, artifacts_before):
            yield json.dumps(artifact) + "\n"
        if speak:
            kokoro = KokoroClient(settings)
            for index, chunk in enumerate(split_spoken_chunks(reply)):
                log_event("tts_start", turn_id=turn_id, started_at=started_at, index=index, text=chunk)
                wav_bytes = kokoro.synthesize(chunk)
                log_event("tts_ready", turn_id=turn_id, started_at=started_at, index=index, bytes=len(wav_bytes))
                yield json.dumps({
                    "type": "audio",
                    "index": index,
                    "text": chunk,
                    "audio": audio_data_uri(wav_bytes),
                }) + "\n"
        yield json.dumps({"type": "done", "turn_id": turn_id}) + "\n"
    except Exception as exc:
        yield json.dumps({"type": "error", "message": str(exc), "turn_id": turn_id}) + "\n"


@app.post("/api/chat-stream")
def chat_stream(request: ChatRequest) -> StreamingResponse:
    message = request.message.strip()
    if not message:
        raise HTTPException(status_code=422, detail="Message is empty")
    return StreamingResponse(
        reply_events(message, request.session_id, request.profile_id, request.speak),
        media_type="application/x-ndjson",
    )


@app.post("/api/converse-stream")
def converse_stream(
    audio: UploadFile = File(...),
    session_id: str = Form("default"),
    profile_id: str = Form("hermes_current"),
    speak: bool = Form(True),
) -> StreamingResponse:
    transcript, turn_id, started_at = transcribe_upload(audio)
    return StreamingResponse(
        reply_events(transcript, session_id, profile_id, speak, turn_id, started_at),
        media_type="application/x-ndjson",
    )


@app.post("/api/tts")
def tts(request: TTSRequest) -> JSONResponse:
    return JSONResponse({"audio": audio_data_uri(KokoroClient().synthesize(request.text))})


@app.get("/artifacts/{session_name}/{artifact_path:path}")
def get_artifact(session_name: str, artifact_path: str) -> FileResponse:
    if not re.fullmatch(r"voice-web-[a-zA-Z0-9_-]{1,64}", session_name):
        raise HTTPException(status_code=404, detail="Artifact not found")
    root = (Path(get_settings().artifact_dir).expanduser().resolve() / session_name).resolve()
    path = (root / artifact_path).resolve()
    if not path.is_relative_to(root) or not path.is_file() or path.is_symlink():
        raise HTTPException(status_code=404, detail="Artifact not found")
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return FileResponse(
        path,
        media_type=mime,
        headers={
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "sandbox; default-src 'none'; img-src 'self' data:; media-src 'self'; style-src 'unsafe-inline'",
        },
    )


@app.post("/api/cancel/{session_id}")
def cancel(session_id: str) -> dict[str, bool]:
    return {"cancelled": cancel_hermes_turn(session_id)}
