"""Post one WAV at several chunk sizes and print how the hub responded.

The hub must already be running. This script changes chunk_ms with PUT /config,
then restores the previous value. It does not import a model library.

    python scripts/sweep_chunks.py --file meeting.wav --chunks 500,1000
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

try:
    from kinig_hub.audio.decode import file_to_pcm_bytes
except ImportError:
    print("Cannot import kinig_hub. From the repo root run: pip install -e hub")
    raise SystemExit(1)


def main() -> int:
    parser = argparse.ArgumentParser(description="Try several chunk sizes against a running hub.")
    parser.add_argument("--file", required=True, help="16 kHz mono s16 WAV, or any WAV the hub can read")
    parser.add_argument("--hub", default="http://127.0.0.1:8000")
    parser.add_argument("--chunks", default="500,1000", help="comma-separated chunk_ms values")
    args = parser.parse_args()

    path = Path(args.file)
    if not path.is_file():
        print(f"File not found: {path}")
        return 1
    audio = file_to_pcm_bytes(path)
    sizes = [int(part) for part in args.chunks.split(",") if part.strip()]
    previous = _get_json(args.hub, "/config")["pod"]["chunk_ms"]
    print(f"chunk_ms was {previous}. It will be restored at the end.")
    try:
        for chunk_ms in sizes:
            _put_json(args.hub, "/config", {"pod": {"chunk_ms": chunk_ms}})
            started = _post_json(args.hub, "/sessions", {"client": "fake"})
            if started["chunk_ms"] != chunk_ms:
                print(f"{chunk_ms}: hub returned chunk_ms {started['chunk_ms']}")
                return 1
            _post_audio(args.hub, started["session_id"], audio, chunk_ms)
            stopped = _post_json(args.hub, f"/sessions/{started['session_id']}/stop", {})
            print(f"chunk_ms {chunk_ms}: status {stopped['status']}  {stopped['notes_url']}")
    finally:
        _put_json(args.hub, "/config", {"pod": {"chunk_ms": previous}})
        print(f"restored chunk_ms {previous}")
    return 0


def _post_audio(hub: str, session_id: str, audio: bytes, chunk_ms: int) -> None:
    width = int(round(chunk_ms * 16000 / 1000)) * 2
    seq = 0
    for start in range(0, len(audio), width):
        piece = audio[start : start + width]
        if len(piece) < width:
            piece += bytes(width - len(piece))
        request = urllib.request.Request(
            f"{hub}/sessions/{session_id}/audio?seq={seq}",
            data=piece,
            method="POST",
            headers={"Content-Type": "application/octet-stream"},
        )
        with urllib.request.urlopen(request, timeout=30):
            pass
        seq += 1


def _get_json(hub: str, path: str) -> dict:
    try:
        with urllib.request.urlopen(hub + path, timeout=10) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        print(f"Could not reach {hub}. Start the hub first. ({exc})")
        raise SystemExit(1) from exc


def _post_json(hub: str, path: str, payload: dict) -> dict:
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        hub + path,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def _put_json(hub: str, path: str, payload: dict) -> dict:
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        hub + path,
        data=body,
        method="PUT",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


if __name__ == "__main__":
    sys.exit(main())
