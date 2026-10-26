"""PCM contract shared by the ESP32, the browser pod, and the hub.

Raw PCM, 16 kHz, signed 16-bit, little-endian, mono, no header.
One POST body is one chunk. Transcribers never see those bytes: they get a
float32 numpy array with samples in -1..1.

Reading a WAV here uses the standard library wave module on purpose.
faster-whisper's decoder stays inside models/, so a bench run does not need
a model just to reject a file in the wrong format.
"""

from __future__ import annotations

import io
import wave
from pathlib import Path

import numpy as np

# Fixed by the audio contract, not by pod.toml. Chunk length is a separate knob.
SAMPLE_RATE = 16_000
SAMPLE_WIDTH_BYTES = 2
CHANNELS = 1
# 16,000 samples/second * 2 bytes = 32,000 bytes per second of audio.
BYTES_PER_SECOND = SAMPLE_RATE * SAMPLE_WIDTH_BYTES

# Printed when a file is missing or not the contract format.
# input.m4a / taglish.wav are the example names from the team doc.
FFMPEG_CONVERT = "ffmpeg -i input.m4a -ar 16000 -ac 1 -sample_fmt s16 taglish.wav"

# int16 range is -32768..32767. Divide by 32768 so -32768 lands on -1.0
# and every other sample stays inside (-1, 1).
_PCM_SCALE = np.float32(32768.0)


class AudioFormatError(ValueError):
    """The bytes are not the PCM this project agreed to send."""


def missing_wav_message(path: Path) -> str:
    return (
        f"Sample audio not found: {path}\n"
        "Put a WAV file there, or run bench_models.py --file <path>.\n"
        "Required format: WAV, 16 kHz, signed 16-bit, mono, little-endian PCM.\n"
        "Convert a recording with:\n"
        f"  {FFMPEG_CONVERT}"
    )


def pcm_bytes_to_float32(data: bytes) -> np.ndarray:
    """Turn one chunk's raw PCM into the array a Transcriber accepts."""
    if len(data) % SAMPLE_WIDTH_BYTES != 0:
        raise AudioFormatError(
            "PCM length is not a multiple of 2 bytes. "
            "Each sample is a signed 16-bit integer, little-endian, with no header."
        )
    samples = np.frombuffer(data, dtype="<i2").astype(np.float32)
    samples /= _PCM_SCALE
    return samples


def float32_to_pcm_bytes(audio: np.ndarray) -> bytes:
    """Pack a float32 array back into raw PCM. Samples outside -1..1 are clipped."""
    clipped = np.clip(np.asarray(audio, dtype=np.float32), -1.0, 1.0)
    # 32767, not 32768: int16 cannot hold +32768.
    scaled = np.round(clipped * np.float32(32767.0)).astype(np.int32).astype("<i2")
    return np.ascontiguousarray(scaled).tobytes()


def pcm_to_wav_bytes(pcm: bytes, sample_rate: int = SAMPLE_RATE) -> bytes:
    """Wrap raw PCM in a WAV header. Debug dumps use this. Models do not."""
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(CHANNELS)
        handle.setsampwidth(SAMPLE_WIDTH_BYTES)
        handle.setframerate(sample_rate)
        handle.writeframes(pcm)
    return buffer.getvalue()


def read_wav_as_float32(path: Path) -> np.ndarray:
    """Read a WAV file into a float32 array, or stop if it is the wrong format.

    Callers pass the array to a Transcriber. This function does not run a model.
    """
    try:
        handle = wave.open(str(path), "rb")
    except wave.Error as exc:
        raise AudioFormatError(f"Could not read {path} as a WAV file: {exc}") from exc

    with handle:
        channels = handle.getnchannels()
        rate = handle.getframerate()
        width = handle.getsampwidth()
        if channels != CHANNELS or rate != SAMPLE_RATE or width != SAMPLE_WIDTH_BYTES:
            width_bits = width * 8
            raise AudioFormatError(
                f"{path} is {rate} Hz, {channels} channel(s), {width_bits}-bit.\n"
                "Kinig needs 16 kHz, mono, signed 16-bit PCM.\n"
                "Convert it with:\n"
                f"  {FFMPEG_CONVERT}"
            )
        pcm = handle.readframes(handle.getnframes())
    return pcm_bytes_to_float32(pcm)
