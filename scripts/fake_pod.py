#!/usr/bin/env python3
"""Send audio to a running hub the way the ESP32 will.

This is a caller. It does not load or choose a model. Chunk length comes
from the POST /sessions response (pod.toml on the hub), never from a
constant in this file.

From the repo root, with the hub already running:

    python scripts/fake_pod.py --file meeting.wav
    python scripts/fake_pod.py --file meeting.wav --realtime
    python scripts/fake_pod.py --file meeting.wav --shuffle --drop 5%
    python scripts/fake_pod.py --mic
    python scripts/fake_pod.py --file meeting.wav --live

--live asks the hub to transcribe during the meeting. The default follows
hub.toml session_mode, which ships as "end" (transcribe after stop).
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

_INSTALL_HELP = (
    "Cannot import kinig_hub. From the repo root run:\n"
    "  pip install -e hub"
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", type=Path, help="WAV or other audio file to send")
    parser.add_argument("--mic", action="store_true", help="stream the laptop microphone")
    parser.add_argument("--realtime", action="store_true", help="pause chunk_ms between posts")
    parser.add_argument("--shuffle", action="store_true", help="send chunks out of order")
    parser.add_argument("--drop", default="0", help="skip this fraction of chunks, e.g. 5%%")
    parser.add_argument("--live", action="store_true", help="ask the hub for live transcription")
    parser.add_argument("--hub", default="http://127.0.0.1:8000", help="hub base URL")
    parser.add_argument("--seed", type=int, default=None, help="random seed for --shuffle/--drop")
    args = parser.parse_args(argv)

    if args.file and args.mic:
        print("Pass either --file or --mic, not both.", file=sys.stderr)
        return 1
    if not args.file and not args.mic:
        print("Pass --file or --mic.", file=sys.stderr)
        return 1

    try:
        from kinig_hub.audio.decode import file_to_pcm_bytes
        from kinig_hub.audio.format import SAMPLE_RATE, AudioFormatError
    except ImportError:
        print(_INSTALL_HELP, file=sys.stderr)
        return 1

    hub = args.hub.rstrip("/")
    mode = "live" if args.live else None
    try:
        started = _start_session(hub, mode)
    except urllib.error.URLError as exc:
        print(f"Could not reach the hub at {hub}: {exc.reason}", file=sys.stderr)
        print("Start it with: python -m kinig_hub.main", file=sys.stderr)
        return 1

    chunk_ms = int(started["chunk_ms"])
    if chunk_ms <= 0:
        print("Hub returned chunk_ms <= 0. Fix pod.toml.", file=sys.stderr)
        return 1
    samples_per_chunk = int(round(chunk_ms * SAMPLE_RATE / 1000))
    session_id = started["session_id"]
    token = started["token"]
    url = started["notes_url"]
    print(f"Session {session_id}  mode={started['mode']}  chunk_ms={chunk_ms}")

    if started["mode"] == "live":
        _print_qr(url)

    try:
        if args.mic:
            sent = _stream_mic(hub, session_id, samples_per_chunk, chunk_ms, args.realtime)
        else:
            try:
                pcm = file_to_pcm_bytes(args.file)
            except (AudioFormatError, RuntimeError) as exc:
                print(exc, file=sys.stderr)
                return 1
            plan = plan_chunks(
                pcm,
                samples_per_chunk,
                shuffle=args.shuffle,
                drop=_parse_drop(args.drop),
                rng=random.Random(args.seed),
            )
            sent = _post_plan(hub, session_id, plan, chunk_ms if args.realtime else 0)
    except urllib.error.URLError as exc:
        print(f"Upload failed: {exc}", file=sys.stderr)
        return 1

    print(f"Posted {sent} chunk(s). Stopping.")
    stopped = _post_json(f"{hub}/sessions/{session_id}/stop", {})
    final = _wait_until_finished(hub, session_id, token)
    print(f"Status: {final.get('status')}  error: {final.get('error') or '(none)'}")
    if started["mode"] != "live":
        _print_qr(stopped.get("notes_url") or url)
    else:
        print(stopped.get("notes_url") or url)
    return 0 if final.get("status") == "done" else 1


def plan_chunks(
    pcm: bytes,
    samples_per_chunk: int,
    *,
    shuffle: bool,
    drop: float,
    rng: random.Random,
) -> list[tuple[int, bytes]]:
    """Pair each slice with its seq, then optionally reorder or omit some.

    seq stays attached to the audio it came from, so a shuffle is a reorder
    test and a drop is a gap test. The hub is responsible for both.
    """
    width = samples_per_chunk * 2
    if width <= 0:
        raise ValueError("samples_per_chunk must be > 0")
    chunks = []
    for seq, start in enumerate(range(0, len(pcm), width)):
        piece = pcm[start : start + width]
        if piece:
            chunks.append((seq, piece))
    if drop > 0 and len(chunks) > 1:
        kept = [item for item in chunks if rng.random() >= drop]
        # Always send something, or the meeting has no audio to score.
        chunks = kept or chunks[:1]
    if shuffle:
        rng.shuffle(chunks)
    return chunks


def _parse_drop(value: str) -> float:
    text = value.strip()
    if text.endswith("%"):
        return float(text[:-1]) / 100
    number = float(text)
    if number > 1:
        return number / 100
    return number


def _start_session(hub: str, mode: str | None) -> dict:
    body: dict = {"client": "fake_pod"}
    if mode:
        body["mode"] = mode
    return _post_json(f"{hub}/sessions", body)


def _post_plan(hub: str, session_id: str, plan: list[tuple[int, bytes]], pause_ms: int) -> int:
    for index, (seq, pcm) in enumerate(plan):
        _post_pcm(hub, session_id, seq, pcm)
        if pause_ms and index + 1 < len(plan):
            time.sleep(pause_ms / 1000)
    return len(plan)


def _stream_mic(hub: str, session_id: str, samples_per_chunk: int, chunk_ms: int, realtime: bool) -> int:
    try:
        import sounddevice as sd
    except ImportError as exc:
        raise RuntimeError(
            "sounddevice is not installed, so --mic cannot run.\n"
            "  pip install -e hub\n"
            "Linux also needs PortAudio (for example the portaudio package)."
        ) from exc
    print("Recording from the laptop mic. Press Ctrl+C to stop.")
    sent = 0
    try:
        with sd.RawInputStream(
            samplerate=16000,
            channels=1,
            dtype="int16",
            blocksize=samples_per_chunk,
        ) as stream:
            while True:
                data, _overflow = stream.read(samples_per_chunk)
                _post_pcm(hub, session_id, sent, bytes(data))
                sent += 1
                if realtime:
                    time.sleep(chunk_ms / 1000)
    except KeyboardInterrupt:
        print()
    return sent


def _post_pcm(hub: str, session_id: str, seq: int, pcm: bytes) -> None:
    request = urllib.request.Request(
        f"{hub}/sessions/{session_id}/audio?seq={seq}",
        data=pcm,
        method="POST",
        headers={"Content-Type": "application/octet-stream"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        response.read()


def _post_json(url: str, payload: dict) -> dict:
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def _wait_until_finished(hub: str, session_id: str, token: str, timeout_s: float = 120) -> dict:
    deadline = time.perf_counter() + timeout_s
    last = {}
    while time.perf_counter() < deadline:
        request = urllib.request.Request(f"{hub}/s/{session_id}/transcript?t={token}")
        with urllib.request.urlopen(request, timeout=10) as response:
            last = json.loads(response.read().decode("utf-8"))
        if last.get("status") in {"done", "error"}:
            return last
        time.sleep(0.2)
    return last


def _print_qr(url: str) -> None:
    print()
    print(url)
    if "://TBD:" in url or url.startswith("http://TBD"):
        local = url.replace("://TBD:", "://127.0.0.1:", 1)
        print("public_host in hub.toml is still TBD, so this QR will not open on a phone.")
        print(f"On this laptop, until the router address is set, open:\n  {local}")
    elif "://127." in url:
        print("This notes URL uses a loopback address. A phone cannot open it.")
        print("Set public_host in hub/config/hub.toml to this laptop's LAN IP, or use auto.")
    try:
        import qrcode
    except ImportError:
        print("(qrcode is not installed, so there is no terminal QR. pip install -e hub)")
        return
    code = qrcode.QRCode(border=1)
    code.add_data(url)
    code.make(fit=True)
    code.print_ascii(invert=True)


if __name__ == "__main__":
    sys.exit(main())
