from __future__ import annotations

from functools import lru_cache
from pathlib import Path
import subprocess

from .config import Settings, get_settings


def transcribe_canary(path: Path, settings: Settings) -> str:
    """Transcribe 16 kHz mono WAV with Handy's Canary 180M Flash Q8_0 port."""
    binary = Path(settings.canary_binary)
    model = Path(settings.canary_model)
    if not binary.is_file():
        raise RuntimeError(f"Canary binary not found: {binary}")
    if not model.is_file():
        raise RuntimeError(f"Canary model not found: {model}")
    output_path = path.with_suffix(".canary.txt")
    cmd = [
        str(binary),
        "--quiet",
        "--backend", "cpu",
        "--threads", str(settings.canary_threads),
        "--model", str(model),
        "--language", settings.canary_language,
        "--pnc",
        "--timestamps", "none",
        "--output", str(output_path),
        str(path),
    ]
    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=60,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"Canary failed with exit {proc.returncode}: {(proc.stderr or proc.stdout)[-2000:]}")
        if not output_path.exists():
            raise RuntimeError("Canary completed without producing a transcript")
        return output_path.read_text(encoding="utf-8").strip()
    finally:
        output_path.unlink(missing_ok=True)


@lru_cache(maxsize=1)
def _load_whisper(model_name: str, device: str, compute_type: str):
    from faster_whisper import WhisperModel

    kwargs = {"device": device}
    if compute_type != "default":
        kwargs["compute_type"] = compute_type
    return WhisperModel(model_name, **kwargs)


def transcribe_whisper(path: Path, settings: Settings) -> str:
    model = _load_whisper(settings.whisper_model, settings.whisper_device, settings.whisper_compute_type)
    segments, _info = model.transcribe(str(path), vad_filter=True, beam_size=1)
    return " ".join(seg.text.strip() for seg in segments if seg.text.strip()).strip()


def transcribe_wav(path: Path, settings: Settings | None = None) -> str:
    settings = settings or get_settings()
    if settings.stt_engine.lower() == "canary":
        try:
            return transcribe_canary(path, settings)
        except Exception as exc:
            print(f"[STT] Canary unavailable; falling back to faster-whisper: {exc}", flush=True)
    return transcribe_whisper(path, settings)
