"""Load the three TOML files under hub/config/.

Pod programs must not hardcode chunk_ms, buffer_count, window_s, or mic_shift.
Those numbers live in config/pod.toml. Phase 2's POST /sessions hands them out.
This module only reads files. It does not choose or run a model.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path


def hub_dir() -> Path:
    """Folder that contains pyproject.toml and config/."""
    return Path(__file__).resolve().parent.parent


def repo_dir() -> Path:
    """Repository root. scripts/ and experiments/ live here, next to hub/."""
    return hub_dir().parent


def whisper_weights_dir() -> Path:
    """Where download_models.py puts the faster-whisper snapshot.

    Runtime loads from this folder only. It does not download.
    """
    return hub_dir() / "model_files" / "whisper"


def resolve_hub_path(relative: str) -> Path:
    """Resolve a path from models.toml. Relative paths start at hub/."""
    path = Path(relative)
    if path.is_absolute():
        return path
    return hub_dir() / path


def default_sample_wav() -> Path:
    """The Taglish benchmark file bench_models.py uses when --file is omitted."""
    return hub_dir() / "tests" / "sample_audio" / "taglish.wav"


@dataclass
class Config:
    """The three config files, still shaped like their TOML tables."""

    hub: dict
    pod: dict
    models: dict


def load_config() -> Config:
    config_dir = hub_dir() / "config"
    return Config(
        hub=_read_toml(config_dir / "hub.toml"),
        pod=_read_toml(config_dir / "pod.toml"),
        models=_read_toml(config_dir / "models.toml"),
    )


def _read_toml(path: Path) -> dict:
    if not path.is_file():
        raise FileNotFoundError(f"Missing config file: {path}")
    with path.open("rb") as handle:
        data = tomllib.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"{path} must be a TOML table")
    return data
