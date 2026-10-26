"""One row per session in experiments/results.csv.

Bench comparisons stay in experiments/bench_results.csv. The columns here are
the pipeline: which settings the pod used, how the upload went, and how far
behind transcription fell.

lag_seconds is the worst window in the session:
    (time from queue until that window finished) minus (the window's length)
A positive number means that window took longer than the audio it contains,
so a live meeting would fall behind.

retries counts a seq posted more than once. The hub never sees a retry that
failed before it arrived.
"""

from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path

from kinig_hub.config import repo_dir

RESULT_COLUMNS = [
    "timestamp",
    "session_id",
    "client",
    "mode",
    "status",
    "chunk_ms",
    "buffer_count",
    "window_s",
    "mic_shift",
    "audio_seconds",
    "chunks_received",
    "missing_seqs",
    "retries",
    "upload_ms_avg",
    "transcribe_seconds",
    "summarize_seconds",
    "lag_seconds",
    "transcriber_backend",
    "summarizer_backend",
    "fallback_used",
]


class MetricsLog:
    def __init__(self, path: Path | None = None):
        self.path = path if path is not None else repo_dir() / "experiments" / "results.csv"

    def write(self, row: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        write_header = not self.path.exists() or self.path.stat().st_size == 0
        with self.path.open("a", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=RESULT_COLUMNS)
            if write_header:
                writer.writeheader()
            stamped = {
                "timestamp": datetime.now().astimezone().isoformat(timespec="seconds"),
                **row,
            }
            writer.writerow({column: stamped.get(column, "") for column in RESULT_COLUMNS})
