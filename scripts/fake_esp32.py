#!/usr/bin/env python3
"""Act like the ESP32 pod: pair, wait for Start on /host, stream, show the QR.

Use it to test the whole flow without the board, or as a demo backup.
The steps are the same ones the firmware runs (firmware/kinig_pod):

    1. POST /pods/hello, print the 4-digit code
    2. poll GET /pods/{id}; the host approves on /host
    3. state "record": stream audio chunks (chunk_ms from the hub)
    4. state "stop": send the last chunk, POST /sessions/{id}/stop
    5. state "show_qr": print the join QR from GET /pods/{id}/qr

From the repo root, with the hub running:

    python scripts/fake_esp32.py --file meeting.wav
    python scripts/fake_esp32.py --mic
    python scripts/fake_esp32.py --file meeting.wav --once   # exit after one session

--hub defaults to http://127.0.0.1:8000. Pass the laptop IP from another machine.
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

POLL_S = 0.7


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--file", type=Path, help="audio file to stream while recording (loops)")
    parser.add_argument("--mic", action="store_true", help="stream the laptop microphone")
    parser.add_argument("--hub", default="http://127.0.0.1:8000", help="hub base URL")
    parser.add_argument("--mac", default=None, help="pretend MAC (default: random per run)")
    parser.add_argument("--once", action="store_true", help="exit after the first session is done")
    args = parser.parse_args(argv)
    if bool(args.file) == bool(args.mic):
        print("Pass exactly one of --file or --mic.", file=sys.stderr)
        return 1

    pcm = b""
    if args.file:
        try:
            from kinig_hub.audio.decode import file_to_pcm_bytes
        except ImportError:
            print("Cannot import kinig_hub. From the repo root run: pip install -e hub", file=sys.stderr)
            return 1
        pcm = file_to_pcm_bytes(args.file)

    hub = args.hub.rstrip("/")
    mac = args.mac or "02:" + ":".join(uuid.uuid4().hex[i : i + 2] for i in range(0, 10, 2))
    pod = Pod(hub, mac, pcm, use_mic=args.mic)
    try:
        return pod.run(once=args.once)
    except KeyboardInterrupt:
        pod.stop_streaming()
        print()
        return 0


class Pod:
    def __init__(self, hub: str, mac: str, pcm: bytes, *, use_mic: bool):
        self.hub = hub
        self.mac = mac
        self.pcm = pcm
        self.use_mic = use_mic
        self.pod_id = ""
        self.shown = ""
        self.streamer: threading.Thread | None = None
        self.streaming = threading.Event()
        self.seq = 0

    def run(self, *, once: bool) -> int:
        self.hello()
        saw_session = False
        while True:
            try:
                state = _get_json(f"{self.hub}/pods/{self.pod_id}")
            except urllib.error.HTTPError as exc:
                if exc.code == 404:
                    print("Hub does not know this pod (restarted?). Saying hello again.")
                    self.stop_streaming()
                    self.hello()
                    continue
                raise
            except urllib.error.URLError as exc:
                self.show(f"Hub unreachable ({exc.reason}). Retrying.")
                time.sleep(2)
                continue

            name = state["state"]
            if name == "pairing":
                self.show(f"PAIR CODE {state['code']}  (approve it on {self.hub}/host)")
            elif name == "ready":
                self.stop_streaming()
                self.show("Ready. Press Start on the host page.")
            elif name == "record":
                saw_session = True
                if not self.streaming.is_set():
                    self.start_streaming(state)
                self.show(f"Recording session {state['session_id'][:8]}  join: {state['join_url']}")
            elif name == "stop":
                self.show("Stopping. Sending the last audio.")
                self.stop_streaming()
                _post_json(f"{self.hub}/sessions/{state['session_id']}/stop", {})
            elif name == "show_qr":
                self.stop_streaming()
                if self.show(f"Scan to join ({state['status']}): {state['join_url']}"):
                    self.print_qr()
                if once and saw_session and state["status"] in {"done", "error"}:
                    return 0 if state["status"] == "done" else 1
            time.sleep(POLL_S)

    def hello(self) -> None:
        while True:
            try:
                body = _post_json(f"{self.hub}/pods/hello", {"mac": self.mac})
                break
            except urllib.error.URLError as exc:
                self.show(f"Looking for the hub at {self.hub} ({exc.reason})")
                time.sleep(2)
        self.pod_id = body["pod_id"]
        self.shown = ""
        print(f"Pod {self.pod_id} said hello.")

    def show(self, line: str) -> bool:
        """Print a status line once, like the OLED redrawing only on change."""
        if line == self.shown:
            return False
        self.shown = line
        print(line)
        return True

    def start_streaming(self, state: dict) -> None:
        self.seq = 0
        self.streaming.set()
        args = (state["session_id"], int(state["chunk_ms"]))
        self.streamer = threading.Thread(target=self._stream, args=args, daemon=True)
        self.streamer.start()

    def stop_streaming(self) -> None:
        self.streaming.clear()
        if self.streamer is not None:
            self.streamer.join(timeout=10)
            self.streamer = None

    def _stream(self, session_id: str, chunk_ms: int) -> None:
        width = chunk_ms * 32  # 16 kHz, 16-bit mono
        if self.use_mic:
            self._stream_mic(session_id, chunk_ms)
            return
        offset = 0
        while self.streaming.is_set():
            piece = self.pcm[offset : offset + width]
            if len(piece) < width:
                piece += self.pcm[: width - len(piece)]
            offset = (offset + width) % max(len(self.pcm), 1)
            self._post_chunk(session_id, piece)
            time.sleep(chunk_ms / 1000)

    def _stream_mic(self, session_id: str, chunk_ms: int) -> None:
        import sounddevice as sd

        samples = chunk_ms * 16
        with sd.RawInputStream(samplerate=16000, channels=1, dtype="int16", blocksize=samples) as stream:
            while self.streaming.is_set():
                data, _overflow = stream.read(samples)
                self._post_chunk(session_id, bytes(data))

    def _post_chunk(self, session_id: str, pcm: bytes) -> None:
        request = urllib.request.Request(
            f"{self.hub}/sessions/{session_id}/audio?seq={self.seq}",
            data=pcm,
            method="POST",
            headers={"Content-Type": "application/octet-stream"},
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                response.read()
            self.seq += 1
        except urllib.error.HTTPError as exc:
            if exc.code == 409:  # host forced the stop; the session is closed
                self.streaming.clear()
                return
            raise

    def print_qr(self) -> None:
        with urllib.request.urlopen(f"{self.hub}/pods/{self.pod_id}/qr", timeout=10) as response:
            body = response.read()
        size = body[0]
        rows = [body[1 + y * size : 1 + (y + 1) * size] for y in range(size)]
        print(f"({size}x{size} modules, as drawn on the OLED)")
        edge = "  " * (size + 2)
        print("\033[47m" + edge + "\033[0m")
        for row in rows:
            line = "".join("\033[40m  " if cell else "\033[47m  " for cell in row)
            print("\033[47m  " + line + "\033[47m  \033[0m")
        print("\033[47m" + edge + "\033[0m")


def _get_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def _post_json(url: str, payload: dict) -> dict:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


if __name__ == "__main__":
    sys.exit(main())
