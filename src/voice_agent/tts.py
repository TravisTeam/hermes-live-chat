from __future__ import annotations

import base64
import os
import re
import time
from pathlib import Path
from urllib.parse import urljoin

import requests

from .config import Settings, get_settings


def split_spoken_chunks(text: str, max_chars: int = 220) -> list[str]:
    """Split a completed reply into natural, bounded chunks for low-latency TTS."""
    normalized = " ".join(text.split())
    if not normalized:
        return []

    sentences = re.split(r"(?<=[.!?])\s+", normalized)
    chunks: list[str] = []
    for sentence in sentences:
        remaining = sentence.strip()
        while len(remaining) > max_chars:
            split_at = remaining.rfind(" ", 0, max_chars + 1)
            if split_at <= 0:
                split_at = max_chars
            chunks.append(remaining[:split_at].strip())
            remaining = remaining[split_at:].strip()
        if remaining:
            chunks.append(remaining)
    # Trailing kaomoji/emoji land in their own sentence chunk; synthesizing those costs a
    # TTS round trip and plays back as noise, so keep only chunks with speakable content.
    return [chunk for chunk in chunks if re.search(r"[^\W_]", chunk, re.UNICODE)]


class KokoroClient:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self.base = self.settings.kokoro_url.rstrip("/") + "/"

    def synthesize(self, text: str) -> bytes:
        payload = {"text": text, "voice": self.settings.kokoro_voice, "speed": self.settings.kokoro_speed}
        r = requests.post(urljoin(self.base, "api/tts/single"), json=payload, timeout=30)
        r.raise_for_status()
        job_id = r.json()["job_id"]
        deadline = time.time() + 120
        last = None
        while time.time() < deadline:
            jr = requests.get(urljoin(self.base, f"api/jobs/{job_id}"), timeout=10)
            jr.raise_for_status()
            data = jr.json()
            last = data
            if data.get("status") in {"done", "completed"}:
                result_path = data.get("result_path")
                if not result_path:
                    raise RuntimeError(f"Kokoro job {job_id} finished without result_path: {data}")
                # Kokoro Studio result_path is inside the container (/data/outputs/...).
                # The API does not currently expose a download route in the OpenAPI schema, so map
                # an optional host-mounted output directory when Kokoro runs in Docker.
                host_candidates = [Path(result_path)]
                output_root = os.getenv("VOICE_AGENT_KOKORO_OUTPUT_DIR")
                if output_root:
                    host_candidates.extend(
                        [
                            Path(output_root) / job_id / "output.wav",
                            Path(output_root) / f"{job_id}.wav",
                        ]
                    )
                for candidate in host_candidates:
                    if candidate.exists():
                        return candidate.read_bytes()
                raise RuntimeError(f"Kokoro output not found on host. result_path={result_path}")
            if data.get("status") in {"failed", "error"}:
                raise RuntimeError(f"Kokoro job failed: {data.get('error') or data}")
            time.sleep(0.25)
        raise TimeoutError(f"Kokoro job timed out; last={last}")


def audio_data_uri(wav_bytes: bytes) -> str:
    return "data:audio/wav;base64," + base64.b64encode(wav_bytes).decode("ascii")
