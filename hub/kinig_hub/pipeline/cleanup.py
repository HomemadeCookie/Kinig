"""Drop session audio once the transcript is stored.

When debug_save_audio is on, a WAV is written first. That flag is for tests.
The in-memory samples are cleared either way.
"""

from __future__ import annotations

from pathlib import Path

from kinig_hub.audio.buffer import AudioBuffer
from kinig_hub.audio.debug_dump import dump_session_wav


def release_audio(
    buffer: AudioBuffer,
    session_id: str,
    *,
    save_debug_wav: bool,
    debug_dir: Path | None = None,
) -> None:
    if save_debug_wav:
        pcm = buffer.debug_bytes()
        if pcm:
            dump_session_wav(session_id, pcm, debug_dir)
    buffer.clear()
