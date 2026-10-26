"""faster-whisper adapter.

The registry hands this class the [transcriber.options] table as a plain dict.
Unknown keys are ignored, so a future non-Whisper adapter is not forced into
Whisper-shaped settings.

Weights are loaded from hub/model_files/whisper with local_files_only=True.
scripts/download_models.py is the one place that is allowed to download, and
only while the laptop still has internet. The hub and the bench never download.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from kinig_hub.config import whisper_weights_dir
from kinig_hub.models.base import Transcriber


class FasterWhisperTranscriber(Transcriber):
    def __init__(self, options: dict):
        self.options = options
        self._model = None

    def model_name(self) -> str:
        name = self.options.get("model")
        if not isinstance(name, str) or not name.strip():
            raise RuntimeError(
                "models.toml is missing transcriber.options.model. "
                'Use a multilingual size such as model = "small" (not "small.en").'
            )
        return name

    def runtime_model_kwargs(self, *, local_files_only: bool) -> dict:
        """Arguments for WhisperModel, built only from keys this adapter knows.

        device and compute_type default for a CPU laptop. Override them from
        [transcriber.options] if you have a GPU. They are not required in the
        TOML file. int8 is the usual CPU setting; float32 is slower.
        """
        return {
            "device": self.options.get("device", "cpu"),
            "compute_type": self.options.get("compute_type", "int8"),
            "download_root": str(whisper_weights_dir()),
            "local_files_only": local_files_only,
        }

    def transcribe_kwargs(self) -> dict:
        """Arguments for WhisperModel.transcribe().

        language "auto" means let Whisper detect it (the library wants None).
        vad_filter defaults to True so a window is less likely to cut a word.
        The pipeline (Phase 2) can still pass a fresh initial_prompt per window
        by calling transcribe on audio that was already cut; this method only
        reads the options loaded from models.toml.
        """
        language = self.options.get("language", "tl")
        if language == "auto":
            language = None
        kwargs = {
            "language": language,
            "task": self.options.get("task", "transcribe"),
            "vad_filter": self.options.get("vad_filter", True),
        }
        prompt = self.options.get("initial_prompt")
        if isinstance(prompt, str) and prompt.strip():
            kwargs["initial_prompt"] = prompt
        return kwargs

    def resolve_transcribe_kwargs(self, initial_prompt: str | None) -> dict:
        """The per-window prompt wins over the one in models.toml.

        The worker passes the previous window's text here so Whisper keeps
        context. The first window passes None and the TOML prompt is used.
        """
        kwargs = self.transcribe_kwargs()
        if isinstance(initial_prompt, str) and initial_prompt.strip():
            kwargs["initial_prompt"] = initial_prompt
        return kwargs

    def load(self) -> None:
        model_cls = _whisper_model_class()
        kwargs = self.runtime_model_kwargs(local_files_only=True)
        name = self.model_name()
        try:
            self._model = model_cls(name, **kwargs)
        except Exception as exc:
            root = kwargs["download_root"]
            raise RuntimeError(
                f"Could not load Whisper model {name!r} from {root}.\n"
                "Runtime never downloads. On a laptop that still has internet, "
                "from the repo root run:\n"
                "  python scripts/download_models.py\n"
                f"Details: {exc}"
            ) from exc

    def download(self) -> None:
        """Download the configured model into model_files/whisper/. Setup only."""
        model_cls = _whisper_model_class()
        kwargs = self.runtime_model_kwargs(local_files_only=False)
        name = self.model_name()
        Path(kwargs["download_root"]).mkdir(parents=True, exist_ok=True)
        try:
            # Constructing the model is how faster-whisper fetches weights.
            model_cls(name, **kwargs)
        except Exception as exc:
            raise RuntimeError(
                f"Could not download Whisper model {name!r} into {kwargs['download_root']}.\n"
                "This setup step needs internet. After it succeeds, the hub and "
                "bench load that folder with local_files_only and will not download.\n"
                f"Details: {exc}"
            ) from exc

    def transcribe(self, audio: np.ndarray, initial_prompt: str | None = None) -> str:
        if not isinstance(audio, np.ndarray):
            raise TypeError(
                "transcribe() expects a float32 numpy array from audio/format.py, "
                "not bytes and not a file path."
            )
        if audio.size == 0:
            return ""
        if self._model is None:
            self.load()
        samples = np.ascontiguousarray(audio, dtype=np.float32)
        segments, _info = self._model.transcribe(
            samples, **self.resolve_transcribe_kwargs(initial_prompt)
        )
        parts = []
        for segment in segments:
            text = segment.text.strip()
            if text:
                parts.append(text)
        return " ".join(parts)


def _whisper_model_class():
    """Import faster-whisper only when a method actually needs it.

    Importing this adapter module must stay cheap, and other code outside
    models/ must never import the library itself.
    """
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise RuntimeError(
            "faster-whisper is not installed. From the repo root run:\n"
            "  pip install -e hub"
        ) from exc
    return WhisperModel
