"""Dev page, /config, and public_host. Models are fakes."""

from __future__ import annotations

import shutil
import time
from pathlib import Path

import numpy as np
from fastapi.testclient import TestClient

from kinig_hub.audio.format import float32_to_pcm_bytes, pcm_to_wav_bytes
from kinig_hub.config import hub_dir, load_config, resolved_public_host
from kinig_hub.main import create_app, https_cert_paths
from kinig_hub.pipeline.worker import ReloadJob, Worker


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
        return {
            "summary": "A short meeting.",
            "key_points": ["point"],
            "action_items": ["Send the deck by Friday."],
        }


def _app(tmp_path: Path, *, dev_mode: bool = False, config_folder: Path | None = None, reread: bool = False):
    config = load_config(config_folder)
    config.hub = dict(config.hub)
    config.pod = dict(config.pod)
    config.hub["dev_mode"] = dev_mode
    config.pod["debug_save_audio"] = False
    return create_app(
        config=config,
        transcriber=_Transcriber(),
        summarizer=_Summarizer(),
        db_path=tmp_path / "sessions.db",
        metrics_path=tmp_path / "results.csv",
        transcriber_name="fake",
        summarizer_name="fake",
        reread_config=reread,
        config_folder=config_folder,
    )


def test_manual_public_host_overrides_auto():
    assert resolved_public_host({"public_host": "192.0.2.55"}) == "192.0.2.55"


def test_auto_public_host_is_not_the_word_auto():
    ip = resolved_public_host({"public_host": "auto"})
    assert ip != "auto"
    assert ip.count(".") == 3


def test_notes_url_uses_a_manual_host(tmp_path: Path):
    config = load_config()
    config.hub = dict(config.hub)
    config.hub["public_host"] = "192.0.2.55"
    app = create_app(
        config=config,
        transcriber=_Transcriber(),
        summarizer=_Summarizer(),
        db_path=tmp_path / "sessions.db",
        metrics_path=tmp_path / "results.csv",
        transcriber_name="fake",
        summarizer_name="fake",
        reread_config=False,
    )
    with TestClient(app) as client:
        body = client.post("/sessions", json={"client": "fake"}).json()
    assert body["notes_url"].startswith("http://192.0.2.55:8000/s/")


def test_dev_page_is_hidden_when_dev_mode_is_off(tmp_path: Path):
    with TestClient(_app(tmp_path, dev_mode=False)) as client:
        assert client.get("/dev").status_code == 404


def test_dev_page_and_scripts_have_no_cdn_and_no_hardcoded_chunk(tmp_path: Path):
    with TestClient(_app(tmp_path, dev_mode=True)) as client:
        page = client.get("/dev")
        assert page.status_code == 200
        assert "pod.js" in page.text
        script = client.get("/static/dev/pod.js")
        assert script.status_code == 200
    root = hub_dir() / "kinig_hub" / "web" / "static" / "dev"
    for name in ("pod.js", "pcm.js", "recorder-worklet.js"):
        text = (root / name).read_text(encoding="utf-8")
        assert "cdn" not in text.lower()
        # Poll delays may use 1000. A chunk length must come from the session.
        assert "chunk_ms =" not in text
        assert "chunkMs =" not in text
    assert "session.chunk_ms" in (root / "pod.js").read_text(encoding="utf-8")
    assert "chunkMs" in (root / "pcm.js").read_text(encoding="utf-8")


def test_put_chunk_ms_is_what_the_next_session_uses(tmp_path: Path):
    folder = tmp_path / "config"
    shutil.copytree(hub_dir() / "config", folder)
    with TestClient(_app(tmp_path, config_folder=folder, reread=True)) as client:
        updated = client.put("/config", json={"pod": {"chunk_ms": 250}})
        assert updated.status_code == 200
        assert updated.json()["pod"]["chunk_ms"] == 250
        started = client.post("/sessions", json={"client": "fake"})
        assert started.json()["chunk_ms"] == 250
    assert "chunk_ms = 250" in (folder / "pod.toml").read_text(encoding="utf-8")
    # The shipped file is a different directory and must stay at the default.
    assert "chunk_ms = 1000" in (hub_dir() / "config" / "pod.toml").read_text(encoding="utf-8")


def test_put_rejects_a_bad_chunk_size(tmp_path: Path):
    folder = tmp_path / "config"
    shutil.copytree(hub_dir() / "config", folder)
    with TestClient(_app(tmp_path, config_folder=folder, reread=True)) as client:
        response = client.put("/config", json={"pod": {"chunk_ms": 10}})
    assert response.status_code == 400
    assert "chunk_ms = 1000" in (folder / "pod.toml").read_text(encoding="utf-8")


def test_file_upload_becomes_a_normal_session_and_the_upload_is_removed(tmp_path: Path):
    wav = pcm_to_wav_bytes(float32_to_pcm_bytes(np.full(1600, 0.2, dtype=np.float32)))
    with TestClient(_app(tmp_path, dev_mode=True)) as client:
        response = client.post(
            "/dev/upload",
            files={"file": ("clip.wav", wav, "audio/wav")},
            data={"mode": "end"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["chunk_ms"] == 1000
        deadline = time.perf_counter() + 5
        notes = {}
        while time.perf_counter() < deadline:
            notes = client.get(f"/s/{body['session_id']}/transcript", params={"t": body["token"]}).json()
            if notes.get("status") == "done":
                break
            time.sleep(0.05)
        assert notes["summary"] == "A short meeting."
        qr = client.get(f"/dev/qr/{body['session_id']}.svg", params={"t": body["token"]})
        assert qr.status_code == 200
        assert qr.text.startswith("<svg")
    leftovers = list(Path(tmp_path).rglob("clip.wav"))
    assert leftovers == []


def test_https_certs_live_under_the_hub_certs_directory():
    cert, key = https_cert_paths()
    assert cert.name == "dev.pem"
    assert key.name == "dev-key.pem"
    assert cert.parent.name == "certs"
    assert cert.parent.parent.name == "hub"


def test_worker_reload_swaps_the_names_it_reports():
    worker = Worker(_Transcriber(), _Summarizer(), transcriber_name="fake", summarizer_name="fake")
    worker.start()
    worker.submit_reload(ReloadJob(_Transcriber(), _Summarizer(), "next-t", "next-s"))
    worker.stop()
    assert worker.transcriber_name == "next-t"
    assert worker.summarizer_name == "next-s"
    assert worker.snapshot("missing")["transcriber"] == "next-t"


def test_pod_network_address_wins_over_the_default_route(monkeypatch):
    import kinig_hub.config as config

    routes = {"192.168.4.1": "192.168.4.2", "192.0.2.1": "10.11.116.156"}
    monkeypatch.setattr(config, "source_ip_for", lambda dest: routes[dest])
    assert config.detect_lan_ip() == "192.168.4.2"

    routes["192.168.4.1"] = "10.11.116.156"  # not on the pod network
    assert config.detect_lan_ip() == "10.11.116.156"
