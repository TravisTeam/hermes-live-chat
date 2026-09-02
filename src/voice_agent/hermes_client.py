from __future__ import annotations

import os
import re
import signal
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path

from .config import get_settings


@dataclass(frozen=True)
class VoiceProfile:
    id: str
    label: str
    hermes_profile: str
    description: str


DEFAULT_PROFILES = [
    VoiceProfile(
        id="hermes_current",
        label="Hermes active model",
        hermes_profile=os.getenv("VOICE_AGENT_HERMES_PROFILE", "default"),
        description="Uses Hermes's current model configuration, tools, memory, skills, and project context.",
    )
]

_NOISE_PREFIXES = ("warning:", "⚠")
_active_lock = threading.Lock()
_active_processes: dict[str, subprocess.Popen[str]] = {}


def load_voice_profiles() -> list[VoiceProfile]:
    return DEFAULT_PROFILES


def profile_by_id(profile_id: str | None) -> VoiceProfile:
    profiles = load_voice_profiles()
    if profile_id == "deepseek_current":
        profile_id = "hermes_current"
    if not profile_id:
        return profiles[0]
    for profile in profiles:
        if profile.id == profile_id:
            return profile
    raise KeyError(f"Unknown voice profile: {profile_id}")


def strip_cli_notices(stdout: str) -> str:
    lines = stdout.splitlines()
    while lines and (not lines[0].strip() or lines[0].strip().lower().startswith(_NOISE_PREFIXES)):
        lines.pop(0)
    return "\n".join(lines).strip()


def _safe_session_name(session_id: str | None) -> str:
    safe = re.sub(r"[^a-zA-Z0-9_-]", "", session_id or "")[:64]
    return f"voice-web-{safe or 'default'}"


def artifact_session_dir(session_id: str | None) -> Path:
    directory = Path(get_settings().artifact_dir).expanduser().resolve() / _safe_session_name(session_id)
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def cancel_hermes_turn(session_id: str) -> bool:
    with _active_lock:
        proc = _active_processes.get(session_id)
    if not proc or proc.poll() is not None:
        return False
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        proc.terminate()
    return True


def run_hermes_turn(
    user_text: str,
    profile_id: str | None = None,
    session_id: str = "default",
    timeout: int | None = None,
) -> tuple[str, VoiceProfile]:
    """Run a turn through a durable named Hermes conversation."""
    timeout = timeout or get_settings().hermes_timeout_seconds
    profile = profile_by_id(profile_id)
    artifact_dir = artifact_session_dir(session_id)
    voice_prompt = (
        "This message is from a private voice interface. Reply naturally for spoken playback. "
        "Be concise unless detail is requested. Avoid markdown tables in spoken replies. "
        "If the user asks you to create or provide an image, document, audio clip, code file, archive, "
        f"or any other downloadable artifact, save the finished file inside {artifact_dir}. "
        "Use a clear filename and mention it naturally in your reply. Files saved there are attached "
        "to the chat automatically; do not paste data URLs or claim a file exists unless you created it. "
        "For long tasks, continue working until the result is complete; the voice interface sends "
        "status updates automatically, so do not stop early merely to report progress.\n\n"
        f"User said: {user_text}"
    )
    cmd = [
        "hermes",
        "--profile",
        profile.hermes_profile,
        "chat",
        "--continue",
        _safe_session_name(session_id),
        "--create-if-missing",
        "--query-file",
        "-",
        "--quiet",
        "--source",
        "voice-agent",
    ]
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    with _active_lock:
        old = _active_processes.get(session_id)
        if old and old.poll() is None:
            try:
                os.killpg(old.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        _active_processes[session_id] = proc
    try:
        stdout, stderr = proc.communicate(voice_prompt, timeout=timeout)
    except subprocess.TimeoutExpired:
        cancel_hermes_turn(session_id)
        proc.wait(timeout=10)
        raise TimeoutError(f"Hermes turn timed out after {timeout} seconds")
    finally:
        with _active_lock:
            if _active_processes.get(session_id) is proc:
                _active_processes.pop(session_id, None)
    if proc.returncode != 0:
        if proc.returncode in (-signal.SIGTERM, 143):
            raise RuntimeError("Hermes response cancelled")
        raise RuntimeError(
            f"Hermes profile '{profile.hermes_profile}' failed with exit {proc.returncode}: "
            f"{(stderr or stdout)[-2000:]}"
        )
    reply = strip_cli_notices(stdout)
    if not reply:
        raise RuntimeError(f"Hermes profile '{profile.hermes_profile}' returned an empty reply")
    return reply, profile
