"""Turn an audio file into the raw PCM the pod would have sent.

WAV files that already match the contract are read with the standard library.
Anything else (m4a, mp3, a WAV at the wrong rate) is decoded with PyAV and
resampled to 16 kHz mono signed 16-bit. This module does not run a model.
fake_pod.py and, later, the dev upload page are the callers.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from kinig_hub.audio.format import (
    SAMPLE_RATE,
    AudioFormatError,
    float32_to_pcm_bytes,
    read_wav_as_float32,
)


def file_to_pcm_bytes(path: Path) -> bytes:
    if path.suffix.lower() == ".wav":
        try:
            return float32_to_pcm_bytes(read_wav_as_float32(path))
        except AudioFormatError:
            # Wrong rate or channel count: resample below instead of refusing.
            pass
    return _decode_with_pyav(path)


def _decode_with_pyav(path: Path) -> bytes:
    try:
        import av
    except ImportError as exc:
        raise RuntimeError(
            "PyAV is not installed, so this file cannot be decoded. "
            "From the repo root run:\n"
            "  pip install -e hub\n"
            "Or convert it first:\n"
            "  ffmpeg -i input.m4a -ar 16000 -ac 1 -sample_fmt s16 taglish.wav"
        ) from exc

    pieces: list[bytes] = []
    container = av.open(str(path))
    try:
        if not container.streams.audio:
            raise AudioFormatError(f"{path} has no audio stream")
        resampler = av.audio.resampler.AudioResampler(
            format="s16",
            layout="mono",
            rate=SAMPLE_RATE,
        )
        for frame in container.decode(audio=0):
            pieces.extend(_frame_bytes(resampler.resample(frame)))
        pieces.extend(_frame_bytes(resampler.resample(None)))
    finally:
        container.close()
    if not pieces:
        raise AudioFormatError(f"{path} decoded to no samples")
    return b"".join(pieces)


def _frame_bytes(converted) -> list[bytes]:
    frames = converted if isinstance(converted, list) else [converted]
    output: list[bytes] = []
    for frame in frames:
        if frame is None:
            continue
        array = np.asarray(frame.to_ndarray())
        # Packed s16 mono is usually shape (1, samples) or (samples,).
        samples = np.ascontiguousarray(array.reshape(-1).astype("<i2"))
        output.append(samples.tobytes())
    return output
