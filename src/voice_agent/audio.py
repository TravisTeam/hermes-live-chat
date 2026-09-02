from __future__ import annotations

import subprocess
from pathlib import Path


def convert_to_wav(input_path: Path, output_path: Path, sample_rate: int = 16000) -> Path:
    """Convert browser-recorded audio to mono WAV for STT using ffmpeg."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(input_path),
        "-ac",
        "1",
        "-ar",
        str(sample_rate),
        "-f",
        "wav",
        str(output_path),
    ]
    try:
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    except FileNotFoundError as exc:
        raise RuntimeError("ffmpeg conversion failed: ffmpeg executable was not found") from exc
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg conversion failed: {proc.stderr[-1000:]}")
    return output_path
