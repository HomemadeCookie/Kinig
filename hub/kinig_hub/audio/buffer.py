"""Per-session audio buffer.

Chunks arrive by `seq` and may be late, repeated, or missing. This buffer
puts them back in order, records gaps, and cuts the audio into windows of
about `window_s` seconds. Whisper runs once per window, not once per chunk.

`window_s` and `chunk_ms` come from the session (pod.toml via POST /sessions).
They are not constants in this file.
"""

from __future__ import annotations

import numpy as np

from kinig_hub.audio.format import SAMPLE_RATE, pcm_bytes_to_float32

# A pause is 200 ms of quiet audio. These are detector settings, not pod
# settings: the pod does not send them, and changing chunk_ms does not change them.
_FRAME_SAMPLES = 320  # 20 ms at 16 kHz
_QUIET_RMS = 0.02
_MIN_QUIET_FRAMES = 10  # 200 ms


class AudioBuffer:
    def __init__(self, window_s: float, chunk_ms: int, *, keep_debug_copy: bool = False):
        if window_s <= 0:
            raise ValueError("window_s must be > 0")
        if chunk_ms <= 0:
            raise ValueError("chunk_ms must be > 0")
        self.window_s = float(window_s)
        self.chunk_ms = int(chunk_ms)
        self.keep_debug_copy = keep_debug_copy
        self._chunks: dict[int, bytes] = {}
        self._next_seq = 0
        self._pending = np.array([], dtype=np.float32)
        self._debug = bytearray() if keep_debug_copy else None
        self.gaps: list[int] = []
        self.duplicate_seqs: list[int] = []
        self.chunks_received = 0
        self.samples_received = 0
        self.upload_ms: list[float] = []

    def add_chunk(self, seq: int, pcm: bytes, upload_ms: float = 0.0) -> None:
        """Store one POST body. A repeated seq is counted and ignored."""
        if seq < 0:
            raise ValueError("seq starts at 0")
        if len(pcm) % 2 != 0:
            raise ValueError("PCM length must be a multiple of 2 bytes")
        self.chunks_received += 1
        self.upload_ms.append(upload_ms)
        if seq < self._next_seq or seq in self._chunks:
            self.duplicate_seqs.append(seq)
            return
        self._chunks[seq] = pcm
        self.samples_received += len(pcm) // 2

    def pop_windows(self, *, final: bool) -> list[np.ndarray]:
        """Return windows that are ready to transcribe.

        While the meeting is going (final=False), only a full window is
        returned, and only from seq numbers that have no hole in front of
        them. A late chunk can still fill that hole.

        On stop (final=True), holes become gaps. Each missing seq is replaced
        with `chunk_ms` of silence so the timeline stays put, and whatever
        audio is left is returned too.
        """
        self._drain(final)
        return self._cut(final)

    def has_audio(self) -> bool:
        return bool(self._chunks) or self._pending.size > 0 or bool(self._debug)

    def debug_bytes(self) -> bytes:
        if self._debug is None:
            return b""
        return bytes(self._debug)

    def clear(self) -> None:
        """Drop every sample. Called after transcription. Text stays in SQLite."""
        self._chunks.clear()
        self._pending = np.array([], dtype=np.float32)
        if self._debug is not None:
            self._debug.clear()

    def _drain(self, final: bool) -> None:
        while self._next_seq in self._chunks:
            self._take(self._next_seq)
        if not final or not self._chunks:
            return
        highest = max(self._chunks)
        while self._next_seq <= highest:
            if self._next_seq in self._chunks:
                self._take(self._next_seq)
                continue
            self.gaps.append(self._next_seq)
            silence = b"\x00\x00" * _samples_for_ms(self.chunk_ms)
            self._append(silence)
            self._next_seq += 1

    def _take(self, seq: int) -> None:
        pcm = self._chunks.pop(seq)
        self._append(pcm)
        self._next_seq += 1

    def _append(self, pcm: bytes) -> None:
        audio = pcm_bytes_to_float32(pcm)
        if self._pending.size == 0:
            self._pending = audio
        else:
            self._pending = np.concatenate([self._pending, audio])
        if self._debug is not None:
            self._debug.extend(pcm)

    def _cut(self, final: bool) -> list[np.ndarray]:
        window = max(1, int(round(self.window_s * SAMPLE_RATE)))
        pieces: list[np.ndarray] = []
        while self._pending.size >= window:
            cut = _pause_cut(self._pending, window)
            if cut is None or cut < 1:
                cut = window
            pieces.append(self._pending[:cut].copy())
            self._pending = self._pending[cut:]
        if final and self._pending.size:
            pieces.append(self._pending.copy())
            self._pending = np.array([], dtype=np.float32)
        return pieces


def _samples_for_ms(chunk_ms: int) -> int:
    return int(round(chunk_ms * SAMPLE_RATE / 1000))


def _pause_cut(samples: np.ndarray, window_samples: int) -> int | None:
    """If the end of this window is quiet, cut at the start of that quiet.

    Cutting on a pause keeps a word from being split across two Whisper calls.
    Continuous speech has no pause, so the caller cuts at `window_samples`.
    """
    start = max(0, window_samples - SAMPLE_RATE // 2)
    end = min(int(samples.size), window_samples)
    if end - start < _FRAME_SAMPLES * _MIN_QUIET_FRAMES:
        return None
    quiet_run = 0
    run_start = 0
    for offset in range(start, end - _FRAME_SAMPLES + 1, _FRAME_SAMPLES):
        frame = samples[offset : offset + _FRAME_SAMPLES]
        rms = float(np.sqrt(np.mean(frame.astype(np.float64) ** 2)))
        if rms < _QUIET_RMS:
            if quiet_run == 0:
                run_start = offset
            quiet_run += 1
            if quiet_run >= _MIN_QUIET_FRAMES:
                return run_start
        else:
            quiet_run = 0
    return None
