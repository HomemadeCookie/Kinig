"""Audio contract: raw PCM and WAV in, float32 array out."""

from __future__ import annotations

import struct
import wave
from pathlib import Path

import numpy as np
import pytest

from kinig_hub.audio.buffer import AudioBuffer
from kinig_hub.audio.decode import file_to_pcm_bytes
from kinig_hub.audio.format import (
    BYTES_PER_SECOND,
    CHANNELS,
    FFMPEG_CONVERT,
    SAMPLE_RATE,
    SAMPLE_WIDTH_BYTES,
    AudioFormatError,
    float32_to_pcm_bytes,
    missing_wav_message,
    pcm_bytes_to_float32,
    pcm_to_wav_bytes,
    read_wav_as_float32,
)


def test_contract_constants():
    assert SAMPLE_RATE == 16_000
    assert SAMPLE_WIDTH_BYTES == 2
    assert CHANNELS == 1
    assert BYTES_PER_SECOND == 32_000


def test_pcm_bytes_to_float32_known_samples():
    pcm = struct.pack("<hhh", 0, 32767, -32768)
    audio = pcm_bytes_to_float32(pcm)
    assert audio.dtype == np.float32
    assert audio.shape == (3,)
    assert audio[0] == 0.0
    assert audio[1] == pytest.approx(32767 / 32768)
    assert audio[2] == -1.0
    assert float(audio.min()) >= -1.0
    assert float(audio.max()) <= 1.0


def test_pcm_rejects_odd_length():
    with pytest.raises(AudioFormatError):
        pcm_bytes_to_float32(b"\x00")


def test_float32_to_pcm_clips_and_is_little_endian():
    audio = np.array([0.0, 1.0, -1.0, 2.0], dtype=np.float32)
    values = np.frombuffer(float32_to_pcm_bytes(audio), dtype="<i2")
    assert list(values) == [0, 32767, -32767, 32767]


def test_wav_roundtrip(tmp_path: Path):
    pcm = struct.pack("<hh", 0, 16384)
    wav_path = tmp_path / "ok.wav"
    wav_path.write_bytes(pcm_to_wav_bytes(pcm))
    with wave.open(str(wav_path), "rb") as handle:
        assert handle.getframerate() == SAMPLE_RATE
        assert handle.getnchannels() == 1
        assert handle.getsampwidth() == 2
    audio = read_wav_as_float32(wav_path)
    assert audio.shape == (2,)
    assert audio[0] == 0.0
    assert audio[1] == pytest.approx(16384 / 32768)


def test_read_wav_rejects_wrong_format(tmp_path: Path):
    wav_path = tmp_path / "phone.wav"
    with wave.open(str(wav_path), "wb") as handle:
        handle.setnchannels(2)
        handle.setsampwidth(2)
        handle.setframerate(44100)
        handle.writeframes(struct.pack("<hhhh", 0, 0, 0, 0))
    with pytest.raises(AudioFormatError) as caught:
        read_wav_as_float32(wav_path)
    message = str(caught.value)
    assert "44100" in message
    assert FFMPEG_CONVERT in message
    assert FFMPEG_CONVERT == (
        "ffmpeg -i input.m4a -ar 16000 -ac 1 -sample_fmt s16 taglish.wav"
    )


def test_missing_wav_message_points_at_the_file():
    path = Path("/tmp/does-not-exist/taglish.wav")
    message = missing_wav_message(path)
    assert str(path) in message
    assert "16 kHz" in message
    assert FFMPEG_CONVERT in message
    assert "--file" in message


def _pcm(n_samples: int, value: float) -> bytes:
    return float32_to_pcm_bytes(np.full(n_samples, value, dtype=np.float32))


def test_buffer_reorders_and_holds_a_gap_until_stop():
    buffer = AudioBuffer(window_s=10, chunk_ms=100)
    buffer.add_chunk(1, _pcm(1600, 0.5))
    buffer.add_chunk(0, _pcm(1600, 0.25))
    # Seq 1 is waiting behind nothing once 0 arrives, but 10 s is not full yet.
    assert buffer.pop_windows(final=False) == []
    windows = buffer.pop_windows(final=True)
    assert buffer.gaps == []
    assert len(windows) == 1
    assert windows[0][0] == pytest.approx(0.25, abs=0.02)
    assert windows[0][1600] == pytest.approx(0.5, abs=0.02)


def test_buffer_records_a_missing_seq_and_counts_retries():
    buffer = AudioBuffer(window_s=10, chunk_ms=100)
    first = _pcm(1600, 0.25)
    buffer.add_chunk(0, first)
    buffer.add_chunk(0, first)
    buffer.add_chunk(2, _pcm(1600, -0.5))
    windows = buffer.pop_windows(final=True)
    assert buffer.gaps == [1]
    assert buffer.duplicate_seqs == [0]
    audio = windows[0]
    # chunk 0, then chunk_ms of silence, then chunk 2
    assert audio[0] == pytest.approx(0.25, abs=0.02)
    assert audio[1600] == pytest.approx(0.0, abs=1e-6)
    assert audio[3200] == pytest.approx(-0.5, abs=0.02)


def test_buffer_cuts_a_window_on_a_pause():
    rate = 16_000
    tone = (0.5 * np.sin(2 * np.pi * 440 * np.arange(12800) / rate)).astype(np.float32)
    silence = np.zeros(3200, dtype=np.float32)
    more = (0.5 * np.sin(2 * np.pi * 440 * np.arange(8000) / rate)).astype(np.float32)
    pcm = float32_to_pcm_bytes(np.concatenate([tone, silence, more]))
    buffer = AudioBuffer(window_s=1, chunk_ms=1000)
    buffer.add_chunk(0, pcm)
    windows = buffer.pop_windows(final=False)
    assert len(windows) == 1
    assert windows[0].size == pytest.approx(12800, abs=320)
    assert buffer.has_audio()


def test_buffer_emits_the_tail_when_the_meeting_stops():
    buffer = AudioBuffer(window_s=1, chunk_ms=1000)
    buffer.add_chunk(0, _pcm(32_000, 0.4))
    windows = buffer.pop_windows(final=True)
    assert [piece.size for piece in windows] == [16_000, 16_000]
    assert buffer.has_audio() is False


def test_decode_resamples_a_wav_that_is_not_16k(tmp_path: Path):
    source = tmp_path / "wide.wav"
    with wave.open(str(source), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(8000)
        handle.writeframes(struct.pack("<h", 1000) * 8000)
    pcm = file_to_pcm_bytes(source)
    audio = pcm_bytes_to_float32(pcm)
    # 1 second at 8 kHz becomes about 1 second at 16 kHz.
    assert audio.size == pytest.approx(16_000, rel=0.05)
