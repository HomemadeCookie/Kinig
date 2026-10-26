"""Summarizer that talks to a local Ollama server over HTTP.

Ollama is a separate program on the laptop (default http://127.0.0.1:11434).
This file does not import an Ollama Python library. If the request fails or
the reply is not the JSON we asked for, it raises. The registry then calls
the fallback summarizer.

The instructions themselves are not in this file. They are loaded from the
prompt_file path in [summarizer.options], usually config/prompts/summary.txt.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from kinig_hub.config import resolve_hub_path
from kinig_hub.models.base import Summarizer

DEFAULT_HOST = "http://127.0.0.1:11434"
_SUMMARY_KEYS = ("summary", "key_points", "action_items")

# Models follow a language name more reliably than a short code.
# Unknown codes are left as written so a new code still reaches the prompt.
_LANGUAGE_NAMES = {
    "en": "English",
    "tl": "Tagalog/Filipino",
}


def language_name(code: str) -> str:
    cleaned = str(code).strip()
    return _LANGUAGE_NAMES.get(cleaned.lower(), cleaned)


class OllamaSummarizer(Summarizer):
    def __init__(self, options: dict):
        self.options = options
        # [summarizer].timeout_s is not inside options. The registry copies it
        # onto this attribute after construction. 60 matches models.toml.
        self.timeout_s = 60
        self.prompt = ""

    def load(self) -> None:
        relative = self.options.get("prompt_file", "config/prompts/summary.txt")
        path = resolve_hub_path(str(relative))
        if not path.is_file():
            raise FileNotFoundError(
                f"Summarizer prompt file not found: {path}\n"
                "The path comes from summarizer.options.prompt_file in models.toml."
            )
        self.prompt = path.read_text(encoding="utf-8")

    def build_prompt(self, transcript: str) -> str:
        if not self.prompt:
            raise RuntimeError("Call load() before summarize(). The prompt file has not been read.")
        # {output_language} in the prompt file is filled from [summarizer.options].
        # The instructions themselves stay in the file, not in this module.
        language = language_name(str(self.options.get("output_language", "en")))
        instructions = self.prompt.replace("{output_language}", language).rstrip()
        return f"{instructions}\n\nTranscript:\n{transcript}"

    def summarize(self, transcript: str) -> dict:
        if not self.prompt:
            self.load()
        model = self.options.get("model")
        if not isinstance(model, str) or not model.strip():
            raise RuntimeError(
                "models.toml is missing summarizer.options.model "
                '(for example model = "llama3.2:3b").'
            )
        host = str(self.options.get("host", DEFAULT_HOST)).rstrip("/")
        body = json.dumps(
            {
                "model": model,
                "prompt": self.build_prompt(transcript),
                "stream": False,
                # Ask Ollama to emit JSON instead of a free-form paragraph.
                "format": "json",
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            f"{host}/api/generate",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Ollama returned HTTP {exc.code}: {detail}") from exc
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Could not reach Ollama at {host}: {exc}") from exc

        text = payload.get("response")
        if not isinstance(text, str):
            raise RuntimeError("Ollama's reply had no text in 'response'.")
        return parse_summary_json(text)

    @staticmethod
    def check_ready(options: dict) -> tuple[bool, str]:
        """Used by scripts/download_models.py. Does not generate a summary."""
        model = options.get("model")
        if not isinstance(model, str) or not model.strip():
            return (
                False,
                "Set summarizer.options.model in models.toml, then run: ollama pull <name>",
            )
        host = str(options.get("host", DEFAULT_HOST)).rstrip("/")
        pull = f"ollama pull {model}"
        try:
            with urllib.request.urlopen(f"{host}/api/tags", timeout=3) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            return (
                False,
                f"Ollama is not reachable at {host} ({exc}).\n"
                "Start Ollama, then pull the model named in models.toml:\n"
                f"  {pull}",
            )
        names = _model_names(payload)
        if model in names:
            return True, f"Ollama model {model} is available."
        return (
            False,
            f"Ollama is running, but {model!r} is not pulled.\n  {pull}",
        )


def parse_summary_json(text: str) -> dict:
    """Pull a summary dict out of an Ollama reply.

    Models sometimes wrap JSON in ``` fences even when format=json was set.
    A missing key raises, so the registry can switch to the fallback.
    """
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = [
            line
            for line in cleaned.splitlines()
            if not line.strip().startswith("```")
        ]
        cleaned = "\n".join(lines).strip()
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError("Summarizer did not return JSON.")
    try:
        data = json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError as exc:
        raise ValueError(f"Summarizer returned invalid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("Summarizer JSON was not an object.")
    missing = [key for key in _SUMMARY_KEYS if key not in data]
    if missing:
        raise ValueError(f"Summarizer JSON is missing {', '.join(missing)}.")
    summary = data["summary"]
    if not isinstance(summary, str):
        summary = str(summary)
    return {
        "summary": summary,
        "key_points": _as_str_list(data["key_points"]),
        "action_items": _as_str_list(data["action_items"]),
    }


def _as_str_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, list):
        return [str(item) for item in value]
    raise ValueError("Expected a list of strings in the summary JSON.")


def _model_names(payload: object) -> set[str]:
    if not isinstance(payload, dict):
        return set()
    found: set[str] = set()
    models = payload.get("models", [])
    if not isinstance(models, list):
        return found
    for item in models:
        if not isinstance(item, dict):
            continue
        for key in ("name", "model"):
            name = item.get(key)
            if isinstance(name, str) and name:
                found.add(name)
    return found
