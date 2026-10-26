#!/usr/bin/env python3
"""Run the models selected in hub/config/models.toml on one WAV file.

Does not start the hub and does not open a session. From the repo root,
after `pip install -e hub`:

    python scripts/bench_models.py
    python scripts/bench_models.py --file path/to/meeting.wav

The default file is hub/tests/sample_audio/taglish.wav. Results are printed
and appended to experiments/bench_results.csv (model comparisons). Session
metrics stay in experiments/results.csv, which Phase 2's metrics.py owns.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from datetime import datetime
from pathlib import Path

# realtime_factor = transcribe_seconds / audio_seconds.
# Above 1.0 means transcription is slower than the recording itself.
BENCH_COLUMNS = [
    "timestamp",
    "file",
    "audio_seconds",
    "transcriber_backend",
    "transcriber_options",
    "summarizer_backend",
    "summarizer_options",
    "transcribe_seconds",
    "summarize_seconds",
    "realtime_factor",
    "fallback_used",
]

_INSTALL_HELP = (
    "Cannot import kinig_hub. From the repo root run:\n"
    "  pip install -e hub"
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--file",
        type=Path,
        default=None,
        help="WAV to score. Default: hub/tests/sample_audio/taglish.wav",
    )
    args = parser.parse_args(argv)

    try:
        from kinig_hub.audio.format import (
            SAMPLE_RATE,
            AudioFormatError,
            missing_wav_message,
            read_wav_as_float32,
        )
        from kinig_hub.config import default_sample_wav, load_config, repo_dir
        from kinig_hub.models.base import validate_summary_result
        from kinig_hub.models.registry import build_summarizer, build_transcriber
    except ImportError:
        print(_INSTALL_HELP, file=sys.stderr)
        return 1

    path = args.file if args.file is not None else default_sample_wav()
    if not path.is_file():
        print(missing_wav_message(path), file=sys.stderr)
        return 1
    try:
        audio = read_wav_as_float32(path)
    except AudioFormatError as exc:
        print(exc, file=sys.stderr)
        return 1

    config = load_config()
    transcriber = build_transcriber(config.models)
    summarizer = build_summarizer(config.models)

    started = time.perf_counter()
    try:
        transcriber.load()
    except Exception as exc:
        print(exc, file=sys.stderr)
        return 1
    load_seconds = time.perf_counter() - started

    started = time.perf_counter()
    try:
        transcript = transcriber.transcribe(audio)
    except Exception as exc:
        print(f"Transcription failed: {exc}", file=sys.stderr)
        return 1
    transcribe_seconds = time.perf_counter() - started

    started = time.perf_counter()
    try:
        summarizer.load()
        summary = validate_summary_result(summarizer.summarize(transcript))
    except Exception as exc:
        print(f"Summarizer failed: {exc}", file=sys.stderr)
        return 1
    summarize_seconds = time.perf_counter() - started

    audio_seconds = len(audio) / SAMPLE_RATE
    if audio_seconds > 0:
        realtime_factor = transcribe_seconds / audio_seconds
    else:
        realtime_factor = None
    fallback_used = bool(getattr(summarizer, "fallback_used", False))

    _print_report(
        path=path,
        audio_seconds=audio_seconds,
        load_seconds=load_seconds,
        transcribe_seconds=transcribe_seconds,
        summarize_seconds=summarize_seconds,
        realtime_factor=realtime_factor,
        transcript=transcript,
        summary=summary,
        transcriber_backend=config.models["transcriber"]["backend"],
        summarizer_backend=config.models["summarizer"]["backend"],
        fallback_used=fallback_used,
        fallback_name=config.models["summarizer"].get("fallback", ""),
        fallback_error=getattr(summarizer, "last_error", None),
    )

    primary = getattr(summarizer, "primary", summarizer)
    row = {
        "timestamp": datetime.now().astimezone().isoformat(timespec="seconds"),
        "file": str(path),
        "audio_seconds": f"{audio_seconds:.3f}",
        "transcriber_backend": config.models["transcriber"]["backend"],
        "transcriber_options": _dump_options(getattr(transcriber, "options", {})),
        "summarizer_backend": config.models["summarizer"]["backend"],
        "summarizer_options": _dump_options(getattr(primary, "options", {})),
        "transcribe_seconds": f"{transcribe_seconds:.3f}",
        "summarize_seconds": f"{summarize_seconds:.3f}",
        "realtime_factor": "" if realtime_factor is None else f"{realtime_factor:.3f}",
        "fallback_used": "true" if fallback_used else "false",
    }
    csv_path = repo_dir() / "experiments" / "bench_results.csv"
    append_bench_row(row, csv_path)
    print(f"\nAppended {csv_path}")
    return 0


def append_bench_row(row: dict, path: Path) -> None:
    """Append one comparison row. Writes the header if the file is new."""
    path.parent.mkdir(parents=True, exist_ok=True)
    write_header = not path.exists() or path.stat().st_size == 0
    with path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=BENCH_COLUMNS)
        if write_header:
            writer.writeheader()
        writer.writerow({column: row.get(column, "") for column in BENCH_COLUMNS})


def _dump_options(options: dict) -> str:
    return json.dumps(options, ensure_ascii=False, sort_keys=True)


def _print_report(
    *,
    path: Path,
    audio_seconds: float,
    load_seconds: float,
    transcribe_seconds: float,
    summarize_seconds: float,
    realtime_factor: float | None,
    transcript: str,
    summary: dict,
    transcriber_backend: str,
    summarizer_backend: str,
    fallback_used: bool,
    fallback_name: str,
    fallback_error: Exception | None,
) -> None:
    if realtime_factor is None:
        factor = "n/a"
    else:
        factor = f"{realtime_factor:.2f}"
    print(f"File: {path}")
    print(f"Audio length: {audio_seconds:.2f} s")
    # Load time is printed so a slow startup is visible, but it is not a CSV
    # column. realtime_factor uses transcription time only.
    print(
        f"Transcriber: {transcriber_backend}  "
        f"load {load_seconds:.2f} s  "
        f"transcribe {transcribe_seconds:.2f} s  "
        f"realtime factor {factor}"
    )
    print()
    print("Transcript:")
    print(transcript or "(empty)")
    print()
    print(
        f"Summarizer: {summarizer_backend}  "
        f"{summarize_seconds:.2f} s  "
        f"fallback_used={'true' if fallback_used else 'false'}"
    )
    if fallback_used:
        print(f"Fallback ({fallback_name}): {fallback_error}")
    print("Summary:")
    print(summary["summary"] or "(empty)")
    print("Key points:")
    _print_items(summary["key_points"])
    print("Action items:")
    _print_items(summary["action_items"])


def _print_items(items: list) -> None:
    if not items:
        print("- (none)")
        return
    for item in items:
        print(f"- {item}")


if __name__ == "__main__":
    sys.exit(main())
