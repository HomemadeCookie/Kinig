"""Build whatever models.toml names.

To add a backend later:
  1. Add one adapter file under models/transcribers/ or models/summarizers/.
  2. Register that class in the dict below.

Do not read Whisper-only keys here. Pass each [*.options] table through as a
dict and let the adapter pick out the keys it understands. timeout_s and
fallback sit on [summarizer], next to the options table, so they are not
stuffed into options.
"""

from __future__ import annotations

import concurrent.futures

from kinig_hub.models.base import Summarizer, Transcriber
from kinig_hub.models.summarizers.ollama import OllamaSummarizer
from kinig_hub.models.summarizers.rule_based import RuleBasedSummarizer
from kinig_hub.models.transcribers.faster_whisper import FasterWhisperTranscriber

TRANSCRIBERS = {
    "faster_whisper": FasterWhisperTranscriber,
}

SUMMARIZERS = {
    "ollama": OllamaSummarizer,
    "rule_based": RuleBasedSummarizer,
}


class FallbackSummarizer(Summarizer):
    """Run the configured summarizer. On error or timeout, use the fallback.

    One Whisper worker is a Phase 2 concern. This wrapper only covers the
    summarizer, which is a single call at the end of a meeting.
    """

    def __init__(self, primary: Summarizer, fallback: Summarizer, timeout_s: float):
        self.primary = primary
        self.fallback = fallback
        self.timeout_s = timeout_s
        self.fallback_used = False
        self.last_error: Exception | None = None

    def load(self) -> None:
        self.primary.load()
        self.fallback.load()

    def summarize(self, transcript: str) -> dict:
        self.fallback_used = False
        self.last_error = None
        try:
            return _call_with_timeout(self.primary.summarize, transcript, self.timeout_s)
        except Exception as exc:
            self.fallback_used = True
            self.last_error = exc
            return self.fallback.summarize(transcript)


def build_transcriber(models_config: dict) -> Transcriber:
    section = _section(models_config, "transcriber")
    backend = _backend_name(section, "transcriber")
    cls = TRANSCRIBERS.get(backend)
    if cls is None:
        known = ", ".join(sorted(TRANSCRIBERS))
        raise ValueError(
            f"Unknown transcriber backend {backend!r}. "
            f"Add an adapter and register it in models/registry.py. Known: {known}"
        )
    return cls(_options(section))


def build_summarizer(models_config: dict) -> Summarizer:
    section = _section(models_config, "summarizer")
    backend = _backend_name(section, "summarizer")
    cls = SUMMARIZERS.get(backend)
    if cls is None:
        known = ", ".join(sorted(SUMMARIZERS))
        raise ValueError(
            f"Unknown summarizer backend {backend!r}. "
            f"Add an adapter and register it in models/registry.py. Known: {known}"
        )
    options = _options(section)
    primary = cls(options)
    # timeout_s belongs to the [summarizer] table, not to a model's options.
    timeout_s = section.get("timeout_s", 60)
    if hasattr(primary, "timeout_s"):
        primary.timeout_s = timeout_s

    fallback_name = section.get("fallback")
    if not fallback_name:
        return primary
    fallback_cls = SUMMARIZERS.get(fallback_name)
    if fallback_cls is None:
        known = ", ".join(sorted(SUMMARIZERS))
        raise ValueError(
            f"Unknown summarizer fallback {fallback_name!r}. Known: {known}"
        )
    # Same options dict shape. The fallback adapter ignores keys it does not use.
    return FallbackSummarizer(primary, fallback_cls(dict(options)), float(timeout_s))


def setup_models(models_config: dict) -> tuple[bool, list[str]]:
    """Download or check the models named in models.toml.

    scripts/download_models.py is the caller. The hub does not call this.
    Returns (ok, lines to print).
    """
    lines: list[str] = []
    ok = True

    transcriber = build_transcriber(models_config)
    if hasattr(transcriber, "download"):
        try:
            transcriber.download()
        except Exception as exc:
            ok = False
            lines.append(str(exc))
        else:
            name = transcriber.options.get("model")
            root = transcriber.runtime_model_kwargs(local_files_only=True)["download_root"]
            lines.append(f"Whisper model {name!r} is ready in {root}.")
    else:
        backend = _backend_name(_section(models_config, "transcriber"), "transcriber")
        lines.append(f"Transcriber {backend!r} has no separate download step.")

    section = _section(models_config, "summarizer")
    backend = _backend_name(section, "summarizer")
    cls = SUMMARIZERS.get(backend)
    checker = getattr(cls, "check_ready", None) if cls is not None else None
    if checker is not None:
        ready, message = checker(_options(section))
        lines.append(message)
        ok = ok and ready
    else:
        lines.append(f"Summarizer {backend!r} does not need a downloaded model.")
    return ok, lines


def _section(models_config: dict, name: str) -> dict:
    section = models_config.get(name)
    if not isinstance(section, dict):
        raise ValueError(f"models.toml is missing [{name}]")
    return section


def _backend_name(section: dict, label: str) -> str:
    backend = section.get("backend")
    if not isinstance(backend, str) or not backend.strip():
        raise ValueError(f"models.toml [{label}] is missing backend")
    return backend


def _options(section: dict) -> dict:
    raw = section.get("options", {})
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError("options must be a TOML table")
    # Copy so an adapter cannot mutate the dict load_config() is holding.
    return dict(raw)


def _call_with_timeout(func, transcript: str, timeout_s: float):
    """Run func(transcript) and raise TimeoutError if it takes too long.

    A timed-out call keeps running in the background until it returns; Python
    cannot safely kill that thread. The HTTP timeout inside Ollama should
    fire around the same time, so the thread does not live much longer.
    """
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    future = pool.submit(func, transcript)
    try:
        return future.result(timeout=timeout_s)
    except concurrent.futures.TimeoutError as exc:
        raise TimeoutError(f"Summarizer timed out after {timeout_s} seconds") from exc
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
