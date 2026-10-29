"""Pod pairing, host Start/Stop, and viewer approval. Models are fakes."""

from __future__ import annotations

import time
from pathlib import Path

import numpy as np
from fastapi.testclient import TestClient

from kinig_hub.audio.format import float32_to_pcm_bytes
from kinig_hub.config import load_config
from kinig_hub.main import create_app
from kinig_hub.pods import normalize_pod_id

MAC = "AA:BB:CC:DD:EE:01"
POD = "aabbccddee01"


class _Transcriber:
    def load(self) -> None:
        return None

    def transcribe(self, audio: np.ndarray, initial_prompt: str | None = None) -> str:
        return "heard"


class _Summarizer:
    fallback_used = False

    def load(self) -> None:
        return None

    def summarize(self, transcript: str) -> dict:
        return {"summary": "Short.", "key_points": ["point"], "action_items": []}


def _app(tmp_path: Path):
    config = load_config()
    config.hub = dict(config.hub)
    config.pod = dict(config.pod)
    config.hub["public_host"] = "192.0.2.10"
    config.pod["debug_save_audio"] = False
    return create_app(
        config=config,
        transcriber=_Transcriber(),
        summarizer=_Summarizer(),
        db_path=tmp_path / "sessions.db",
        metrics_path=tmp_path / "results.csv",
        transcriber_name="fake",
        summarizer_name="fake",
        reread_config=False,
    )


def _pair(client: TestClient) -> dict:
    hello = client.post("/pods/hello", json={"mac": MAC}).json()
    assert client.post(f"/host/pods/{POD}/approve").status_code == 200
    return hello


def _chunk(chunk_ms: int) -> bytes:
    return float32_to_pcm_bytes(np.full(chunk_ms * 16, 0.1, dtype=np.float32))


def _wait_status(client: TestClient, want: str) -> dict:
    state = {}
    for _ in range(100):
        state = client.get(f"/pods/{POD}").json()
        if state.get("status") == want:
            return state
        time.sleep(0.05)
    raise AssertionError(state)


def test_mac_is_normalized():
    assert normalize_pod_id("AA:BB:CC:DD:EE:01") == "aabbccddee01"
    assert normalize_pod_id("") is None


def test_pod_waits_for_approval_with_a_code_the_host_also_sees(tmp_path: Path):
    with TestClient(_app(tmp_path)) as client:
        hello = client.post("/pods/hello", json={"mac": MAC}).json()
        assert hello["pod_id"] == POD
        assert len(hello["code"]) == 4 and hello["code"].isdigit()
        assert client.get(f"/pods/{POD}").json()["state"] == "pairing"

        host = client.get("/host/state").json()
        assert host["pods"][0]["code"] == hello["code"]
        assert host["pods"][0]["online"] is True

        client.post(f"/host/pods/{POD}/approve")
        assert client.get(f"/pods/{POD}").json()["state"] == "ready"


def test_start_cannot_run_before_pairing(tmp_path: Path):
    with TestClient(_app(tmp_path)) as client:
        client.post("/pods/hello", json={"mac": MAC})
        assert client.post(f"/host/pods/{POD}/start").status_code == 409


def test_unknown_pod_gets_404_so_it_says_hello_again(tmp_path: Path):
    with TestClient(_app(tmp_path)) as client:
        assert client.get(f"/pods/{POD}").status_code == 404


def test_host_start_stop_drives_the_pod_end_to_end(tmp_path: Path):
    with TestClient(_app(tmp_path)) as client:
        _pair(client)
        started = client.post(f"/host/pods/{POD}/start", json={"mode": "end"}).json()
        state = client.get(f"/pods/{POD}").json()
        assert state["state"] == "record"
        assert state["session_id"] == started["session_id"]
        assert state["join_url"] == f"http://192.0.2.10:8000/j/{started['session_id'][:8]}"
        chunk_ms = state["chunk_ms"]

        for seq in range(2):
            response = client.post(
                f"/sessions/{state['session_id']}/audio",
                params={"seq": seq},
                content=_chunk(chunk_ms),
            )
            assert response.status_code == 200

        client.post(f"/host/pods/{POD}/stop")
        assert client.get(f"/pods/{POD}").json()["state"] == "stop"

        # The pod sends its last chunk, then closes the session itself.
        client.post(f"/sessions/{state['session_id']}/audio", params={"seq": 2}, content=_chunk(chunk_ms))
        assert client.post(f"/sessions/{state['session_id']}/stop").status_code == 200

        done = _wait_status(client, "done")
        assert done["state"] == "show_qr"

        client.post(f"/host/pods/{POD}/clear")
        assert client.get(f"/pods/{POD}").json()["state"] == "ready"


def test_pressing_stop_twice_forces_the_session_closed(tmp_path: Path):
    with TestClient(_app(tmp_path)) as client:
        _pair(client)
        client.post(f"/host/pods/{POD}/start")
        assert client.post(f"/host/pods/{POD}/stop").json()["forced"] is False
        assert client.post(f"/host/pods/{POD}/stop").json()["forced"] is True
        assert client.get(f"/pods/{POD}").json()["state"] == "show_qr"


def test_a_rebooted_pod_stays_paired_and_its_old_session_is_closed(tmp_path: Path):
    with TestClient(_app(tmp_path)) as client:
        _pair(client)
        session_id = client.post(f"/host/pods/{POD}/start").json()["session_id"]
        again = client.post("/pods/hello", json={"mac": MAC}).json()
        assert again["paired"] is True
        assert client.get(f"/pods/{POD}").json()["state"] == "ready"
        response = client.post(f"/sessions/{session_id}/audio", params={"seq": 0}, content=_chunk(100))
        assert response.status_code == 409


def test_qr_bytes_are_a_square_matrix_small_enough_for_the_oled(tmp_path: Path):
    with TestClient(_app(tmp_path)) as client:
        _pair(client)
        client.post(f"/host/pods/{POD}/start")
        body = client.get(f"/pods/{POD}/qr").content
        size = body[0]
        assert len(body) == 1 + size * size
        assert set(body[1:]) <= {0, 1}
        # Version 3 is 29 modules: 2 pixels each, plus a margin, fits 64 rows.
        assert size <= 29


def test_viewer_waits_until_the_host_approves(tmp_path: Path):
    with TestClient(_app(tmp_path)) as client:
        _pair(client)
        session_id = client.post(f"/host/pods/{POD}/start").json()["session_id"]
        code = session_id[:8]

        assert client.get(f"/j/{code}").status_code == 200
        joined = client.post(f"/j/{code}", data={"name": "  Juan   Dela Cruz "}, follow_redirects=False)
        assert joined.status_code == 303
        viewer_id = joined.headers["location"].rsplit("/", 1)[-1]
        assert client.get(f"/w/{viewer_id}").status_code == 200

        assert client.get(f"/w/{viewer_id}/state").json() == {"status": "pending"}
        viewers = client.get("/host/state").json()["sessions"][0]["viewers"]
        assert viewers == [{"viewer_id": viewer_id, "name": "Juan Dela Cruz", "status": "pending"}]

        client.post(f"/host/viewers/{viewer_id}/approve")
        state = client.get(f"/w/{viewer_id}/state").json()
        assert state["status"] == "approved"
        assert client.get(state["notes_path"]).status_code == 200


def test_denied_viewer_never_gets_a_notes_link(tmp_path: Path):
    with TestClient(_app(tmp_path)) as client:
        _pair(client)
        session_id = client.post(f"/host/pods/{POD}/start").json()["session_id"]
        joined = client.post(f"/j/{session_id[:8]}", data={"name": ""}, follow_redirects=False)
        viewer_id = joined.headers["location"].rsplit("/", 1)[-1]
        client.post(f"/host/viewers/{viewer_id}/deny")
        assert client.get(f"/w/{viewer_id}/state").json() == {"status": "denied"}
        assert client.get("/host/state").json()["sessions"][0]["viewers"][0]["name"] == "Guest"


def test_old_or_wrong_join_code_is_404(tmp_path: Path):
    with TestClient(_app(tmp_path)) as client:
        assert client.get("/j/deadbeef").status_code == 404
        assert client.get("/j/../etc").status_code == 404


def test_host_page_and_home_redirect(tmp_path: Path):
    with TestClient(_app(tmp_path)) as client:
        assert "Kinig host" in client.get("/").text
        assert client.get("/host/qr/missing.svg").status_code == 404


def test_host_state_has_what_the_oled_preview_needs(tmp_path: Path):
    with TestClient(_app(tmp_path)) as client:
        _pair(client)
        session_id = client.post(f"/host/pods/{POD}/start").json()["session_id"]
        pod = client.get("/host/state").json()["pods"][0]
        assert pod["session_status"] == "recording"
        assert pod["session_created_at"]
        preview = client.get(f"/host/qr/{session_id}.bin").content
        assert preview == client.get(f"/pods/{POD}/qr").content
