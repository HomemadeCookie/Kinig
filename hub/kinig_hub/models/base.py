"""The only two model interfaces the rest of the hub is allowed to call.

Concrete libraries (faster-whisper, Ollama, ...) stay inside their adapter
files. Pipeline code asks a Transcriber or a Summarizer to do the work.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

import numpy as np

SUMMARY_KEYS = ("summary", "key_points", "action_items")


class Transcriber(ABC):
    def load(self) -> None:
        """Load weights once. Override when the backend has something to load."""
        return None

    @abstractmethod
    def transcribe(self, audio: np.ndarray, initial_prompt: str | None = None) -> str:
        """Turn one utterance into text.

        audio is float32, mono, 16 kHz, samples in -1..1, from audio/format.py.
        Do not accept a file path or raw PCM bytes here.

        initial_prompt is optional context from the previous window. None means
        "use whatever prompt the adapter was configured with."
        """


class Summarizer(ABC):
    def load(self) -> None:
        """Prepare the backend once. Override when there is a prompt or weights."""
        return None

    @abstractmethod
    def summarize(self, transcript: str) -> dict:
        """Return summary, key_points, and action_items."""


def validate_summary_result(result: dict) -> dict:
    """Check the dict shape every summarizer promises to return."""
    if not isinstance(result, dict):
        raise TypeError("summarize() must return a dict")
    missing = [key for key in SUMMARY_KEYS if key not in result]
    if missing:
        raise ValueError(f"summarize() is missing keys: {', '.join(missing)}")
    if not isinstance(result["summary"], str):
        raise TypeError("summary must be a string")
    for key in ("key_points", "action_items"):
        if not isinstance(result[key], list):
            raise TypeError(f"{key} must be a list")
    return result
