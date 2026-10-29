#!/usr/bin/env python3
"""Mock Kinig: the real web UI with fake pods, fake viewers, and canned notes.

No ESP32, no Whisper, no Ollama. Use it to look at /host, the join page, the
waiting page, and the notes page, and to rehearse the demo.

    python scripts/mock_demo.py                 # you click everything on /host
    python scripts/mock_demo.py --auto          # mock flow plays by itself, on a loop
    python scripts/mock_demo.py --port 8080 --pods 2

Manual mode: two pods appear waiting for pairing. When you start a recording,
fake viewers (Maria, Paolo, Juan) ask to join a few seconds later. You can
also scan the join QR with a real phone on the same Wi-Fi.

The database is a temporary file. Nothing is kept after Ctrl+C.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import random
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

LINES = [
    "Okay, so yung deadline natin for the prototype is Friday.",
    "Niccolo will finish the firmware, tapos i-test natin yung pairing sa laptop.",
    "We need the pitch deck updated before the midsem consultation.",
    "Sino mag-present ng demo? Let's have Bernard do the live part.",
    "Budget for the enclosure reprint is around five hundred pesos.",
]
VIEWERS = ["Maria", "Paolo", "Juan"]


class MockTranscriber:
    def __init__(self):
        self.index = 0

    def load(self) -> None:
        return None

    def transcribe(self, audio: np.ndarray, initial_prompt: str | None = None) -> str:
        line = LINES[self.index % len(LINES)]
        self.index += 1
        return line


class MockSummarizer:
    fallback_used = False

    def load(self) -> None:
        return None

    def summarize(self, transcript: str) -> dict:
        time.sleep(1.5)  # so "summarizing" shows up on the pages
        return {
            "summary": "The team set Friday as the prototype deadline and split the firmware, testing, and pitch work.",
            "key_points": [
                "Prototype deadline is Friday.",
                "Pairing will be tested on the laptop before the demo.",
                "Enclosure reprint costs about PHP 500.",
            ],
            "action_items": [
                "Niccolo: finish the pod firmware.",
                "Bernard: run the live demo.",
                "Team: update the pitch deck before the midsem consultation.",
            ],
        }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--pods", type=int, default=2, help="fake pods to show (1-4)")
    parser.add_argument("--auto", action="store_true", help="play the whole flow by itself, on a loop")
    args = parser.parse_args()

    import uvicorn

    from kinig_hub.config import load_config, resolved_public_host
    from kinig_hub.main import create_app

    folder = Path(tempfile.mkdtemp(prefix="kinig-mock-"))
    config = load_config()
    config.hub = dict(config.hub)
    config.pod = dict(config.pod)
    config.hub["http_port"] = args.port
    config.pod["window_s"] = 4
    config.pod["debug_save_audio"] = False
    app = create_app(
        config=config,
        transcriber=MockTranscriber(),
        summarizer=MockSummarizer(),
        db_path=folder / "mock.db",
        metrics_path=folder / "results.csv",
        transcriber_name="mock",
        summarizer_name="mock",
        reread_config=False,
    )
    server = uvicorn.Server(uvicorn.Config(app, host="0.0.0.0", port=args.port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    base = f"http://127.0.0.1:{args.port}"
    _wait_for(base)

    lan = resolved_public_host(config.hub)
    print(f"Mock hub running. Open http://127.0.0.1:{args.port}/host")
    print(f"Phones on this Wi-Fi can scan the join QR (http://{lan}:{args.port}/j/...).")
    print("Ctrl+C to quit.\n")

    for n in range(max(1, min(args.pods, 4))):
        _start_fake_pod(base, f"02:00:00:00:00:{n + 1:02x}")

    try:
        if args.auto:
            _auto_flow(base)
        else:
            _manual_helpers(base)
    except KeyboardInterrupt:
        print()
    return 0


def _start_fake_pod(base: str, mac: str) -> None:
    from fake_esp32 import Pod

    pod = Pod(base, mac, bytes(32000), use_mic=False)  # one second of silence, looped
    threading.Thread(target=_quiet, args=(pod.run,), kwargs={"once": False}, daemon=True).start()


def _quiet(func, **kwargs):
    with contextlib.redirect_stdout(io.StringIO()):
        func(**kwargs)


def _manual_helpers(base: str) -> None:
    """Whenever a pod starts recording, fake viewers ask to join."""
    seen: set[str] = set()
    while True:
        state = _get(f"{base}/host/state")
        for pod in state["pods"]:
            sid = pod.get("session_id")
            if pod["state"] == "record" and sid and sid not in seen:
                seen.add(sid)
                threading.Thread(target=_viewers_join, args=(base, sid), daemon=True).start()
        time.sleep(1)


def _viewers_join(base: str, session_id: str) -> None:
    for name in VIEWERS:
        time.sleep(random.uniform(2, 5))
        _post_form(f"{base}/j/{session_id[:8]}", {"name": name})
        print(f"[mock] {name} asked to join session {session_id[:8]}")


def _auto_flow(base: str) -> None:
    """Pair, start, viewers join, stop, approve, clear. Repeats."""
    print("[auto] Mock flow: watch /host. It loops every ~45 s.")
    pod_id = ""
    while not pod_id:
        pods = _get(f"{base}/host/state")["pods"]
        pod_id = pods[0]["pod_id"] if pods else ""
        time.sleep(0.5)
    while True:
        _step("Pod shows its pairing code. Approving it.", 5)
        _post(f"{base}/host/pods/{pod_id}/approve")
        _step("Pod is ready. Starting a recording.", 4)
        _post(f"{base}/host/pods/{pod_id}/start", {"mode": "end"})
        sid = ""
        while not sid:
            sid = _get(f"{base}/pods/{pod_id}").get("session_id", "")
            time.sleep(0.3)
        _step("Recording. Viewers scan the QR and ask to join.", 2)
        _viewers_join(base, sid)
        _step("Stopping the recording.", 6)
        _post(f"{base}/host/pods/{pod_id}/stop")
        _step("Approving Maria and Paolo, denying Juan.", 4)
        for viewer in _get(f"{base}/host/state")["sessions"][0]["viewers"]:
            decision = "deny" if viewer["name"] == "Juan" else "approve"
            _post(f"{base}/host/viewers/{viewer['viewer_id']}/{decision}")
        _step("Notes are ready on approved phones. Clearing the pod.", 10)
        _post(f"{base}/host/pods/{pod_id}/clear")
        _step("Forgetting the pod so the loop shows pairing again.", 4)
        _post(f"{base}/host/pods/{pod_id}/forget")
        time.sleep(3)


def _step(message: str, pause: float) -> None:
    print(f"[auto] {message}")
    time.sleep(pause)


def _wait_for(base: str) -> None:
    for _ in range(100):
        try:
            _get(f"{base}/host/state")
            return
        except OSError:
            time.sleep(0.1)
    raise SystemExit("Mock hub did not start. Is the port already in use? Try --port 8090.")


def _get(url: str) -> dict:
    import json

    with urllib.request.urlopen(url, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def _post(url: str, payload: dict | None = None) -> None:
    import json

    request = urllib.request.Request(
        url,
        data=json.dumps(payload or {}).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with contextlib.suppress(urllib.error.HTTPError):
        urllib.request.urlopen(request, timeout=10).read()


def _post_form(url: str, fields: dict) -> None:
    data = urllib.parse.urlencode(fields).encode("utf-8")
    request = urllib.request.Request(url, data=data, method="POST")
    with contextlib.suppress(urllib.error.HTTPError):
        urllib.request.urlopen(request, timeout=10).read()


if __name__ == "__main__":
    sys.exit(main())
