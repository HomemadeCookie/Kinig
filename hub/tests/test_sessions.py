"""Session API, the background worker, and fake_pod as a caller.

Models here are fakes. The production server builds them from models.toml.
Nothing in this file imports faster-whisper or talks to Ollama.
"""

from __future__ import annotations

import importlib.util
import random
import threading
import time
from pathlib import Path

import numpy as np
import pytest
import uvicorn
from fastapi.testclient import TestClient

from kinig_hub.audio.format import float32_to_pcm_bytes, pcm_to_wav_bytes
from kinig_hub.config import load_config, repo_dir
from kinig_hub.main import create_app
from kinig_hub.models.base import validate_summary_result


class _Transcriber:
    def __init__(self):
        self.prompts: list[str | None] = []
        self.delay_s = 0.0

    def load(self) -> None:
        return None

    def transcribe(self, audio: np.ndarray, initial_prompt: str | None = None) -> str:
        if self.delay_s:
            time.sleep(self.delay_s)
        self.prompts.append(initial_prompt)
        return f"heard {audio[0]:.2f}"


class _Summarizer:
    def __init__(self):
        self.fallback_used = False

    def load(self) -> None:
        return None

    def summarize(self, transcript: str) -> dict:
        return validate_summary_result(
            {
                "summary": "A short meeting.",
                "key_points": [transcript] if transcript else [],
                "action_items": ["Send the deck by Friday."],
            }
        )


def _app(tmp_path: Path, *, pod: dict | None = None, transcriber=None):
    config = load_config()
    config.hub = dict(config.hub)
    config.pod = dict(config.pod)
    if pod:
        config.pod.update(pod)
    config.pod["debug_save_audio"] = False
    app = create_app(
        config=config,
        transcriber=transcriber or _Transcriber(),
        summarizer=_Summarizer(),
        db_path=tmp_path / "sessions.db",
        metrics_path=tmp_path / "results.csv",
        transcriber_name="fake",
        summarizer_name="fake",
        reread_config=False,
    )
    return app


def _pcm(seconds: float, value: float) -> bytes:
    n = int(seconds * 16_000)
    return float32_to_pcm_bytes(np.full(n, value, dtype=np.float32))


def _wait(client: TestClient, session_id: str, token: str) -> dict:
    last = {}
    for _ in range(100):
        response = client.get(f"/s/{session_id}/transcript", params={"t": token})
        assert response.status_code == 200
        last = response.json()
        if last["status"] in {"done", "error"}:
            return last
        time.sleep(0.05)
    raise AssertionError(last)


def test_start_returns_pod_settings_from_the_config_file(tmp_path: Path):
    app = _app(tmp_path)
    pod = load_config().pod
    with TestClient(app) as client:
        response = client.post("/sessions", json={"client": "fake_pod"})
        assert response.status_code == 200
        body = response.json()
        assert body["chunk_ms"] == pod["chunk_ms"]
        assert body["buffer_count"] == pod["buffer_count"]
        assert body["window_s"] == pod["window_s"]
        assert body["mic_shift"] == pod["mic_shift"]
        assert body["mode"] == "end"
        assert body["notes_url"].startswith("http://")
        assert "://auto:" not in body["notes_url"]
        assert "localhost" not in body["notes_url"]


def test_browser_client_is_tagged_by_who_connected():
    from kinig_hub.api.sessions import refine_client

    assert refine_client("browser", "127.0.0.1") == "browser-laptop"
    assert refine_client("browser", "192.168.4.20") == "browser-phone"
    assert refine_client("esp32", "127.0.0.1") == "esp32"


def test_end_mode_transcribes_after_stop_then_deletes_audio(tmp_path: Path):
    transcriber = _Transcriber()
    app = _app(tmp_path, pod={"window_s": 10, "chunk_ms": 1000}, transcriber=transcriber)
    with TestClient(app) as client:
        started = client.post("/sessions", json={"client": "fake_pod", "mode": "end"}).json()
        session_id = started["session_id"]
        token = started["token"]
        # Out of order on purpose. Seq 0 is the quieter chunk.
        assert client.post(
            f"/sessions/{session_id}/audio", params={"seq": 1}, content=_pcm(0.5, 0.5)
        ).status_code == 200
        early = client.get(f"/s/{session_id}/transcript", params={"t": token}).json()
        assert early["status"] == "recording"
        assert early["transcript"] == ""
        assert client.post(
            f"/sessions/{session_id}/audio", params={"seq": 0}, content=_pcm(0.5, 0.25)
        ).status_code == 200
        # A repeat counts as a retry and must not be transcribed twice.
        assert client.post(
            f"/sessions/{session_id}/audio", params={"seq": 0}, content=_pcm(0.5, 0.25)
        ).status_code == 200

        stopped = client.post(f"/sessions/{session_id}/stop")
        assert stopped.status_code == 200
        assert stopped.json()["notes_url"] == started["notes_url"]

        final = _wait(client, session_id, token)
        assert final["status"] == "done"
        assert final["transcript"].startswith("heard 0.25")
        assert final["summary"] == "A short meeting."
        assert final["action_items"] == ["Send the deck by Friday."]

        page = client.get(f"/s/{session_id}", params={"t": token})
        assert page.status_code == 200
        assert "A short meeting." in page.text
        assert "Send the deck by Friday." in page.text
        assert client.get(f"/s/{session_id}", params={"t": "nope"}).status_code == 403
        assert client.get(f"/s/{session_id}/transcript").status_code == 403

        buffer = app.state.hub.buffers[session_id]
        assert buffer.has_audio() is False

    rows = (tmp_path / "results.csv").read_text(encoding="utf-8").splitlines()
    header = rows[0].split(",")
    values = _csv_row(rows[1])
    assert header[0] == "timestamp"
    assert values[header.index("client")] == "fake_pod"
    # One duplicate seq was posted. That is the retry count.
    assert values[header.index("retries")] == "1"
    assert values[header.index("status")] == "done"
    assert values[header.index("fallback_used")] == "false"
    assert transcriber.prompts[0] is None


def test_audio_post_returns_before_a_slow_transcriber_finishes(tmp_path: Path):
    transcriber = _Transcriber()
    transcriber.delay_s = 0.4
    app = _app(tmp_path, pod={"window_s": 1, "chunk_ms": 1000}, transcriber=transcriber)
    with TestClient(app) as client:
        started = client.post("/sessions", json={"client": "fake_pod", "mode": "live"}).json()
        started_at = time.perf_counter()
        response = client.post(
            f"/sessions/{started['session_id']}/audio",
            params={"seq": 0},
            content=_pcm(1.0, 0.4),
        )
        elapsed = time.perf_counter() - started_at
        assert response.status_code == 200
        assert response.json() == {"ok": True}
        assert elapsed < 0.25
        deadline = time.perf_counter() + 3
        while not transcriber.prompts and time.perf_counter() < deadline:
            time.sleep(0.05)
        assert transcriber.prompts, "live mode should transcribe before stop"
        client.post(f"/sessions/{started['session_id']}/stop")
        final = _wait(client, started["session_id"], started["token"])
        assert final["status"] == "done"
        assert "heard 0.40" in final["transcript"]


def test_live_mode_passes_the_previous_window_as_prompt(tmp_path: Path):
    transcriber = _Transcriber()
    app = _app(tmp_path, pod={"window_s": 1, "chunk_ms": 1000}, transcriber=transcriber)
    with TestClient(app) as client:
        started = client.post("/sessions", json={"client": "browser", "mode": "live"}).json()
        assert started["client"] == "browser-phone"
        client.post(
            f"/sessions/{started['session_id']}/audio",
            params={"seq": 0},
            content=_pcm(2.0, 0.3),
        )
        deadline = time.perf_counter() + 3
        while len(transcriber.prompts) < 2 and time.perf_counter() < deadline:
            time.sleep(0.05)
        assert transcriber.prompts[0] is None
        assert transcriber.prompts[1] == "heard 0.30"
        client.post(f"/sessions/{started['session_id']}/stop")
        final = _wait(client, started["session_id"], started["token"])
        assert final["status"] == "done"
        assert "heard 0.30" in final["transcript"]


def test_fake_pod_script_sends_a_file(tmp_path: Path):
    app = _app(tmp_path, pod={"window_s": 5, "chunk_ms": 500})
    port = _free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        _wait_for_port(port)
        wav = tmp_path / "meeting.wav"
        wav.write_bytes(pcm_to_wav_bytes(_pcm(1.0, 0.2)))
        fake_pod = _load_fake_pod()
        code = fake_pod.main(
            ["--file", str(wav), "--hub", f"http://127.0.0.1:{port}", "--seed", "1"]
        )
        assert code == 0
    finally:
        server.should_exit = True
        thread.join(timeout=5)

    text = (tmp_path / "results.csv").read_text(encoding="utf-8")
    assert "fake_pod" in text
    assert "done" in text
    # The server process is this process, so the buffer is still on the app.
    buffers = list(app.state.hub.buffers.values())
    assert buffers
    assert all(buffer.has_audio() is False for buffer in buffers)


def test_plan_chunks_can_shuffle_and_drop():
    fake_pod = _load_fake_pod()
    pcm = b"\x01\x00" * 4000  # 5 chunks of 800 samples
    shuffled = False
    for seed in range(20):
        plan = fake_pod.plan_chunks(
            pcm,
            samples_per_chunk=800,
            shuffle=True,
            drop=0,
            rng=random.Random(seed),
        )
        seqs = [seq for seq, _piece in plan]
        assert sorted(seqs) == [0, 1, 2, 3, 4]
        shuffled = shuffled or seqs != [0, 1, 2, 3, 4]
    assert shuffled
    dropped = fake_pod.plan_chunks(
        pcm,
        samples_per_chunk=800,
        shuffle=False,
        drop=0.5,
        rng=random.Random(0),
    )
    assert 0 < len(dropped) < 5


def _csv_row(line: str) -> list[str]:
    import csv
    from io import StringIO

    return next(csv.reader(StringIO(line)))


def _free_port() -> int:
    import socket

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _wait_for_port(port: int) -> None:
    import socket

    deadline = time.perf_counter() + 5
    while time.perf_counter() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return
        except OSError:
            time.sleep(0.05)
    raise AssertionError(f"hub did not listen on {port}")


def _load_fake_pod():
    path = repo_dir() / "scripts" / "fake_pod.py"
    spec = importlib.util.spec_from_file_location("kinig_fake_pod", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
