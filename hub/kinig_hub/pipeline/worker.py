"""One background thread that transcribes, then summarizes.

Whisper uses the whole CPU, so a second job at the same time would only make
both slower. A single queue also means "finalize" cannot run until every
window queued before it has been transcribed.

The FastAPI handlers only put jobs on this queue. They do not call the model.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from dataclasses import dataclass

import numpy as np

from kinig_hub.audio.format import SAMPLE_RATE
from kinig_hub.models.base import Summarizer, Transcriber, validate_summary_result
from kinig_hub.pipeline.cleanup import release_audio

log = logging.getLogger("kinig.worker")


@dataclass
class TranscribeJob:
    session_id: str
    audio: np.ndarray
    queued_at: float


@dataclass
class FinalizeJob:
    session_id: str


@dataclass
class ReloadJob:
    """Swap models after PUT /config. The worker loads them between jobs."""

    transcriber: Transcriber
    summarizer: Summarizer
    transcriber_name: str
    summarizer_name: str


class Worker:
    def __init__(
        self,
        transcriber: Transcriber,
        summarizer: Summarizer,
        *,
        transcriber_name: str,
        summarizer_name: str,
    ):
        self.transcriber = transcriber
        self.summarizer = summarizer
        self.transcriber_name = transcriber_name
        self.summarizer_name = summarizer_name
        self.hub = None  # set by create_app; the worker needs the buffers and storage
        self._queue: queue.Queue = queue.Queue()
        self._thread: threading.Thread | None = None
        self._last_text: dict[str, str] = {}
        self._timing: dict[str, dict] = {}
        self._load_error = ""

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="kinig-worker", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._queue.put(None)
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def submit_transcribe(self, session_id: str, audio: np.ndarray) -> None:
        self._queue.put(TranscribeJob(session_id, audio, time.perf_counter()))

    def submit_finalize(self, session_id: str) -> None:
        self._queue.put(FinalizeJob(session_id))

    def submit_reload(self, job: ReloadJob) -> None:
        self._queue.put(job)

    def snapshot(self, session_id: str) -> dict:
        """Read-only view for the dev status panel. Copied so the page cannot mutate it."""
        timing = self._timing.get(session_id) or {}
        lags = list(timing.get("lags") or [])
        return {
            "transcriber": self.transcriber_name,
            "summarizer": self.summarizer_name,
            "lag_seconds": max(lags) if lags else None,
            "load_error": self._load_error,
        }

    def _run(self) -> None:
        try:
            self.transcriber.load()
            self.summarizer.load()
        except Exception as exc:
            # Keep the thread alive so stop() can still delete audio and write metrics.
            self._load_error = str(exc)
            log.exception("model load failed")
        while True:
            job = self._queue.get()
            if job is None:
                return
            try:
                if isinstance(job, TranscribeJob):
                    self._transcribe(job)
                elif isinstance(job, FinalizeJob):
                    self._finalize(job)
                elif isinstance(job, ReloadJob):
                    self._reload(job)
            except Exception:
                log.exception("job failed for session %s", getattr(job, "session_id", "?"))

    def _reload(self, job: ReloadJob) -> None:
        self.transcriber = job.transcriber
        self.summarizer = job.summarizer
        self.transcriber_name = job.transcriber_name
        self.summarizer_name = job.summarizer_name
        try:
            self.transcriber.load()
            self.summarizer.load()
            self._load_error = ""
        except Exception as exc:
            self._load_error = str(exc)
            log.exception("model reload failed")

    def _timing_for(self, session_id: str) -> dict:
        row = self._timing.get(session_id)
        if row is None:
            row = {"transcribe_s": 0.0, "lags": [], "failed": False, "error": ""}
            self._timing[session_id] = row
        return row

    def _transcribe(self, job: TranscribeJob) -> None:
        assert self.hub is not None
        timing = self._timing_for(job.session_id)
        if self._load_error:
            timing["failed"] = True
            timing["error"] = self._load_error
            return
        prompt = self._last_text.get(job.session_id)
        started = time.perf_counter()
        try:
            text = self.transcriber.transcribe(job.audio, initial_prompt=prompt)
        except Exception as exc:
            timing["failed"] = True
            timing["error"] = str(exc)
            log.exception("transcription failed")
            return
        elapsed = time.perf_counter() - started
        timing["transcribe_s"] += elapsed
        audio_s = len(job.audio) / SAMPLE_RATE
        # Queue wait is included: a backed-up worker shows up as lag.
        timing["lags"].append((time.perf_counter() - job.queued_at) - audio_s)
        if text.strip():
            self._last_text[job.session_id] = text.strip()
            self.hub.storage.append_transcript(job.session_id, text)

    def _finalize(self, job: FinalizeJob) -> None:
        assert self.hub is not None
        hub = self.hub
        timing = self._timing_for(job.session_id)
        session = hub.storage.get_session(job.session_id)
        if session is None:
            return
        hub.storage.set_status(job.session_id, "summarizing")
        if timing["failed"] or self._load_error:
            hub.storage.set_error(job.session_id, timing["error"] or self._load_error)
        # Every transcribe job queued before this one has already finished,
        # so the transcript on `session` is the full meeting.
        summary = None
        fallback_used = False
        started = time.perf_counter()
        try:
            summary = validate_summary_result(self.summarizer.summarize(session["transcript"]))
            fallback_used = bool(getattr(self.summarizer, "fallback_used", False))
        except Exception as exc:
            timing["failed"] = True
            timing["error"] = timing["error"] or str(exc)
            hub.storage.set_error(job.session_id, timing["error"])
            log.exception("summarize failed")
        summarize_s = time.perf_counter() - started
        if summary is not None:
            hub.storage.save_notes(job.session_id, summary)
        status = "error" if timing["failed"] else "done"
        # Drop the audio and write the metrics row before status becomes
        # done, so a client that sees "done" is not racing the cleanup.
        with hub.buffer_lock:
            buffer = hub.buffers.get(job.session_id)
            stats = _buffer_stats(buffer)
            if buffer is not None:
                release_audio(
                    buffer,
                    job.session_id,
                    save_debug_wav=buffer.keep_debug_copy,
                )
        lags = timing["lags"]
        hub.metrics.write(
            {
                "session_id": job.session_id,
                "client": session["client"],
                "mode": session["mode"],
                "status": status,
                "chunk_ms": session["chunk_ms"],
                "buffer_count": session["buffer_count"],
                "window_s": session["window_s"],
                "mic_shift": session["mic_shift"],
                "audio_seconds": f"{stats['audio_seconds']:.3f}",
                "chunks_received": stats["chunks_received"],
                "missing_seqs": "|".join(str(seq) for seq in stats["gaps"]),
                "retries": stats["retries"],
                "upload_ms_avg": f"{stats['upload_ms_avg']:.1f}",
                "transcribe_seconds": f"{timing['transcribe_s']:.3f}",
                "summarize_seconds": f"{summarize_s:.3f}",
                "lag_seconds": f"{(max(lags) if lags else 0.0):.3f}",
                "transcriber_backend": self.transcriber_name,
                "summarizer_backend": self.summarizer_name,
                "fallback_used": "true" if fallback_used else "false",
            }
        )
        hub.storage.set_status(job.session_id, status)
        self._last_text.pop(job.session_id, None)
        self._timing.pop(job.session_id, None)


def _buffer_stats(buffer) -> dict:
    if buffer is None:
        return {
            "audio_seconds": 0.0,
            "chunks_received": 0,
            "gaps": [],
            "retries": 0,
            "upload_ms_avg": 0.0,
        }
    uploads = buffer.upload_ms
    average = sum(uploads) / len(uploads) if uploads else 0.0
    return {
        "audio_seconds": buffer.samples_received / SAMPLE_RATE,
        "chunks_received": buffer.chunks_received,
        "gaps": list(buffer.gaps),
        "retries": len(buffer.duplicate_seqs),
        "upload_ms_avg": average,
    }
