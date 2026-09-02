from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Settings:
    llm_base_url: str = os.getenv("VOICE_AGENT_LLM_BASE_URL", "http://127.0.0.1:8000/v1")
    llm_api_key: str = os.getenv("VOICE_AGENT_LLM_API_KEY", "not-needed")
    llm_model: str = os.getenv("VOICE_AGENT_LLM_MODEL", "local-model")
    model_server_url: str = os.getenv("VOICE_AGENT_MODEL_SERVER_URL", "http://127.0.0.1:8888")
    kokoro_url: str = os.getenv("VOICE_AGENT_KOKORO_URL", "http://127.0.0.1:8880")
    kokoro_voice: str = os.getenv("VOICE_AGENT_KOKORO_VOICE", "af_heart")
    kokoro_speed: float = float(os.getenv("VOICE_AGENT_KOKORO_SPEED", "1.0"))
    whisper_model: str = os.getenv("VOICE_AGENT_WHISPER_MODEL", "base")
    whisper_device: str = os.getenv("VOICE_AGENT_WHISPER_DEVICE", "auto")
    whisper_compute_type: str = os.getenv("VOICE_AGENT_WHISPER_COMPUTE_TYPE", "default")
    stt_engine: str = os.getenv("VOICE_AGENT_STT_ENGINE", "canary")
    canary_binary: str = os.getenv(
        "VOICE_AGENT_CANARY_BINARY",
        str(PROJECT_ROOT / "vendor" / "transcribe.cpp" / "build" / "bin" / "transcribe-cli"),
    )
    canary_model: str = os.getenv(
        "VOICE_AGENT_CANARY_MODEL",
        str(PROJECT_ROOT / "models" / "canary-180m-flash" / "canary-180m-flash-Q8_0.gguf"),
    )
    canary_language: str = os.getenv("VOICE_AGENT_CANARY_LANGUAGE", "en")
    canary_threads: int = int(os.getenv("VOICE_AGENT_CANARY_THREADS", "8"))
    hermes_timeout_seconds: int = int(os.getenv("VOICE_AGENT_HERMES_TIMEOUT_SECONDS", "1800"))
    hermes_progress_interval_seconds: float = float(
        os.getenv("VOICE_AGENT_HERMES_PROGRESS_INTERVAL_SECONDS", "60")
    )
    temp_dir: str = os.getenv("VOICE_AGENT_TEMP_DIR", "/tmp/hermes-live-chat")
    artifact_dir: str = os.getenv(
        "VOICE_AGENT_ARTIFACT_DIR",
        str(Path.home() / ".local" / "share" / "hermes-live-chat" / "artifacts"),
    )
    system_prompt: str = os.getenv(
        "VOICE_AGENT_SYSTEM_PROMPT",
        "You are a private local voice assistant. Keep spoken replies concise, natural, and useful.",
    )


def get_settings() -> Settings:
    return Settings()
