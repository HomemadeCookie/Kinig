"""Write a session WAV only when debug_save_audio is true.

The demo leaves that flag off. This file is the only place a WAV is kept,
and only for testing. cleanup.py still drops the in-memory audio either way.
"""

from __future__ import annotations

from pathlib import Path

from kinig_hub.audio.format import pcm_to_wav_bytes
from kinig_hub.config import hub_dir


def debug_audio_dir() -> Path:
    return hub_dir() / "debug_audio"


def dump_session_wav(session_id: str, pcm: bytes, directory: Path | None = None) -> Path:
    folder = directory if directory is not None else debug_audio_dir()
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{session_id}.wav"
    path.write_bytes(pcm_to_wav_bytes(pcm))
    return path
