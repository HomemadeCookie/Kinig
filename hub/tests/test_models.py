"""Model interfaces, registry, and the offline bench script.

These tests do not download weights and do not need Ollama to be running.
Whisper and Ollama are checked through their adapters' edges (options in,
JSON out, fallback when the server is down).
"""

from __future__ import annotations

import ast
import importlib.util
import time
from pathlib import Path

import numpy as np
import pytest

from kinig_hub.audio.format import pcm_to_wav_bytes
from kinig_hub.config import load_config, repo_dir, resolve_hub_path
from kinig_hub.models.registry import (
    FallbackSummarizer,
    build_summarizer,
    build_transcriber,
)
from kinig_hub.models.summarizers.ollama import (
    OllamaSummarizer,
    language_name,
    parse_summary_json,
)
from kinig_hub.models.summarizers.rule_based import RuleBasedSummarizer
from kinig_hub.models.transcribers.faster_whisper import FasterWhisperTranscriber

_GOOD_SUMMARY = {
    "summary": "A greeting.",
    "key_points": ["Hello"],
    "action_items": [],
}


def test_shipped_config_has_the_sections_the_hub_needs():
    config = load_config()
    for key in ("chunk_ms", "buffer_count", "window_s", "mic_shift"):
        assert isinstance(config.pod[key], int)
        assert config.pod[key] > 0
    # Keeping audio would contradict the privacy claim. The demo leaves this off.
    assert config.pod["debug_save_audio"] is False
    assert config.hub["http_port"] == 8000
    assert config.hub["https_port"] == 8443
    assert isinstance(config.hub["dev_mode"], bool)
    assert isinstance(config.hub["dev_https"], bool)
    assert config.hub["session_mode"] in {"end", "live"}
    assert isinstance(config.hub["public_host"], str)
    assert config.models["transcriber"]["backend"] == "faster_whisper"
    assert config.models["transcriber"]["options"]["model"] == "base"
    assert config.models["summarizer"]["backend"] == "ollama"
    assert config.models["summarizer"]["options"]["model"] == "llama3.2:1b"
    assert config.models["summarizer"]["fallback"] == "rule_based"


def test_prompt_is_loaded_from_the_path_in_models_toml():
    config = load_config()
    options = config.models["summarizer"]["options"]
    path = resolve_hub_path(options["prompt_file"])
    file_text = path.read_text(encoding="utf-8")
    assert "Taglish" in file_text
    assert "{output_language}" in file_text

    summarizer = OllamaSummarizer(dict(options))
    summarizer.load()
    assert summarizer.prompt == file_text
    built = summarizer.build_prompt("Mag-usap tayo tungkol sa demo.")
    assert "Write the notes in this language: English." in built
    assert "{output_language}" not in built
    assert "Mag-usap tayo tungkol sa demo." in built


def test_registry_passes_options_through_and_does_not_invent_whisper_keys():
    options = {"model": "small", "custom_key": "keep me"}
    transcriber = build_transcriber(
        {"transcriber": {"backend": "faster_whisper", "options": options}}
    )
    assert transcriber.options == options
    assert "language" not in transcriber.options
    assert "vad_filter" not in transcriber.options


def test_unknown_backend_names_the_ones_that_exist():
    with pytest.raises(ValueError, match="whisper_cpp"):
        build_transcriber({"transcriber": {"backend": "whisper_cpp", "options": {}}})


def test_faster_whisper_reads_only_its_own_keys():
    transcriber = FasterWhisperTranscriber(
        {
            "model": "small",
            "device": "cpu",
            "compute_type": "int8",
            "language": "auto",
            "task": "transcribe",
            "initial_prompt": "Taglish meeting.",
            "not_a_whisper_arg": True,
        }
    )
    model_kwargs = transcriber.runtime_model_kwargs(local_files_only=True)
    assert model_kwargs["local_files_only"] is True
    assert model_kwargs["device"] == "cpu"
    assert model_kwargs["compute_type"] == "int8"
    assert model_kwargs["download_root"].endswith("model_files/whisper")
    assert "not_a_whisper_arg" not in model_kwargs

    call = transcriber.transcribe_kwargs()
    assert call["language"] is None  # "auto" lets Whisper detect the language
    assert call["task"] == "transcribe"
    assert call["vad_filter"] is True
    assert call["initial_prompt"] == "Taglish meeting."
    assert "not_a_whisper_arg" not in call
    # A previous window's text replaces the prompt from models.toml for that call.
    over = transcriber.resolve_transcribe_kwargs("previous window")
    assert over["initial_prompt"] == "previous window"


def test_transcribe_rejects_bytes_and_empty_audio():
    transcriber = FasterWhisperTranscriber({"model": "small"})
    with pytest.raises(TypeError):
        transcriber.transcribe(b"\x00\x00")
    assert transcriber.transcribe(np.array([], dtype=np.float32)) == ""


def test_adapter_module_does_not_import_faster_whisper_at_the_top():
    # The library import stays inside load/download/transcribe. Importing the
    # adapter (as the registry does at startup) must not pull it in.
    path = importlib_path(FasterWhisperTranscriber)
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
            assert "faster_whisper" not in names
        if isinstance(node, ast.ImportFrom):
            assert node.module != "faster_whisper"


def test_scripts_do_not_import_model_libraries():
    for path in sorted((repo_dir() / "scripts").glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name.split(".")[0] for alias in node.names]
                assert "faster_whisper" not in names
            if isinstance(node, ast.ImportFrom) and node.module:
                assert not node.module.startswith("faster_whisper")


def test_language_name_maps_codes_and_keeps_unknown_values():
    assert language_name("en") == "English"
    assert language_name("TL") == "Tagalog/Filipino"
    assert language_name("ceb") == "ceb"
    assert language_name("English") == "English"


def test_rule_based_summary_shape_and_action_guess():
    summarizer = RuleBasedSummarizer({"model": "ignored", "prompt_file": "nope.txt"})
    summarizer.load()
    result = summarizer.summarize(
        "We need to ship the demo by Friday. The booth is in room 2. "
        "Lance will update the BOM."
    )
    assert set(result) == {"summary", "key_points", "action_items"}
    assert result["summary"].startswith("We need to ship the demo by Friday.")
    assert len(result["key_points"]) == 3
    assert any("need to" in item for item in result["action_items"])
    assert any("will" in item for item in result["action_items"])
    assert summarizer.summarize("   ") == {
        "summary": "",
        "key_points": [],
        "action_items": [],
    }
    # The booth sentence has none of the cues, including "by Friday".
    assert "booth" not in " ".join(result["action_items"])


def test_rule_based_taglish_cues():
    summarizer = RuleBasedSummarizer({})
    sentences = [
        "Ito dapat ay handa bago mag-demo.",
        "Pakiusap i-follow up yung vendor.",
        "Send the deck by Friday.",
        "The writeup is assigned to the design group.",
        "si Lance will open the laptop.",
        "The booth is in room 2.",
    ]
    found = []
    for sentence in sentences:
        summary = summarizer.summarize(sentence)
        if summary["action_items"]:
            found.append(sentence)
    assert found == sentences[:-1]


def test_parse_summary_json_accepts_fences_and_rejects_a_missing_key():
    parsed = parse_summary_json(
        '```json\n{"summary": "Done", "key_points": ["A"], "action_items": []}\n```'
    )
    assert parsed == {"summary": "Done", "key_points": ["A"], "action_items": []}
    with pytest.raises(ValueError):
        parse_summary_json('{"summary": "only one field"}')


def test_fallback_runs_only_when_the_primary_fails():
    fallback = _FakeSummarizer(_GOOD_SUMMARY)
    ok = FallbackSummarizer(_FakeSummarizer(_GOOD_SUMMARY), fallback, timeout_s=2)
    assert ok.summarize("hello")["summary"] == "A greeting."
    assert ok.fallback_used is False

    broken = FallbackSummarizer(_ExplodingSummarizer(), _FakeSummarizer(_GOOD_SUMMARY), timeout_s=2)
    assert broken.summarize("hello")["summary"] == "A greeting."
    assert broken.fallback_used is True
    assert isinstance(broken.last_error, RuntimeError)


def test_fallback_on_timeout():
    wrapper = FallbackSummarizer(_SlowSummarizer(), _FakeSummarizer(_GOOD_SUMMARY), timeout_s=0.1)
    started = time.perf_counter()
    result = wrapper.summarize("hello")
    elapsed = time.perf_counter() - started
    assert result["summary"] == "A greeting."
    assert wrapper.fallback_used is True
    assert elapsed < 1.0


def test_ollama_down_uses_rule_based_fallback():
    prompt = load_config().models["summarizer"]["options"]["prompt_file"]
    summarizer = build_summarizer(
        {
            "summarizer": {
                "backend": "ollama",
                "fallback": "rule_based",
                "timeout_s": 2,
                "options": {
                    "model": "missing-model",
                    "host": "http://127.0.0.1:9",
                    "prompt_file": prompt,
                    "output_language": "en",
                },
            }
        }
    )
    assert isinstance(summarizer, FallbackSummarizer)
    assert summarizer.timeout_s == 2
    assert summarizer.primary.timeout_s == 2
    result = summarizer.summarize("We need to rehearse the demo tomorrow.")
    assert result["summary"]
    assert summarizer.fallback_used is True


def test_shipped_models_toml_builds_without_loading_weights():
    config = load_config()
    transcriber = build_transcriber(config.models)
    summarizer = build_summarizer(config.models)
    assert isinstance(transcriber, FasterWhisperTranscriber)
    assert transcriber.options["model"] == config.models["transcriber"]["options"]["model"]
    assert isinstance(summarizer, FallbackSummarizer)
    assert summarizer.timeout_s == config.models["summarizer"]["timeout_s"]


def test_bench_missing_file_explains_where_to_put_it(capsys):
    bench = _load_bench()
    code = bench.main(["--file", "/tmp/kinig-missing-taglish.wav"])
    assert code == 1
    error = capsys.readouterr().err
    assert "taglish.wav" in error
    assert "ffmpeg -i input.m4a -ar 16000 -ac 1 -sample_fmt s16 taglish.wav" in error


def test_bench_appends_a_row_without_calling_a_real_model(tmp_path, monkeypatch, capsys):
    wav_path = tmp_path / "clip.wav"
    wav_path.write_bytes(pcm_to_wav_bytes(b"\x00\x00" * 16000))
    monkeypatch.setattr("kinig_hub.config.repo_dir", lambda: tmp_path)
    monkeypatch.setattr(
        "kinig_hub.models.registry.build_transcriber",
        lambda _config: _FakeTranscriber(),
    )
    monkeypatch.setattr(
        "kinig_hub.models.registry.build_summarizer",
        lambda _config: _FakeSummarizer(_GOOD_SUMMARY),
    )

    bench = _load_bench()
    code = bench.main(["--file", str(wav_path)])
    assert code == 0
    printed = capsys.readouterr().out
    assert "Hello from the fake transcriber." in printed
    assert "A greeting." in printed
    assert "realtime factor" in printed

    csv_path = tmp_path / "experiments" / "bench_results.csv"
    lines = csv_path.read_text(encoding="utf-8").splitlines()
    assert lines[0] == ",".join(bench.BENCH_COLUMNS)
    assert "faster_whisper" in lines[1]
    assert "Hello" not in lines[0]
    assert "fallback_used" in lines[0]
    assert lines[1].endswith("false") or ",false" in lines[1]


class _FakeTranscriber:
    options = {"model": "small", "language": "tl"}

    def load(self) -> None:
        return None

    def transcribe(self, audio: np.ndarray) -> str:
        assert isinstance(audio, np.ndarray)
        assert audio.dtype == np.float32
        assert len(audio) == 16000
        return "Hello from the fake transcriber."


class _FakeSummarizer:
    def __init__(self, result: dict):
        self._result = result
        self.options = {"model": "fake"}
        self.fallback_used = False

    def load(self) -> None:
        return None

    def summarize(self, transcript: str) -> dict:
        assert transcript
        return dict(self._result)


class _ExplodingSummarizer:
    options: dict = {}

    def load(self) -> None:
        return None

    def summarize(self, transcript: str) -> dict:
        raise RuntimeError("model down")


class _SlowSummarizer:
    options: dict = {}

    def load(self) -> None:
        return None

    def summarize(self, transcript: str) -> dict:
        time.sleep(0.4)
        return {"summary": "too late", "key_points": [], "action_items": []}


def importlib_path(cls: type) -> Path:
    module = importlib.util.find_spec(cls.__module__)
    assert module is not None and module.origin is not None
    return Path(module.origin)


def _load_bench():
    path = repo_dir() / "scripts" / "bench_models.py"
    spec = importlib.util.spec_from_file_location("kinig_bench_models", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
