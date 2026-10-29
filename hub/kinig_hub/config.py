"""Load the three TOML files under hub/config/.

Pod programs must not hardcode chunk_ms, buffer_count, window_s, or mic_shift.
Those numbers live in config/pod.toml. POST /sessions hands them out.
This module only reads files. It does not choose or run a model.
"""

from __future__ import annotations

import logging
import socket
import tomllib
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("kinig.config")


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


def config_dir() -> Path:
    return hub_dir() / "config"


def load_config(folder: Path | None = None) -> Config:
    directory = folder if folder is not None else config_dir()
    return Config(
        hub=_read_toml(directory / "hub.toml"),
        pod=_read_toml(directory / "pod.toml"),
        models=_read_toml(directory / "models.toml"),
    )


def resolved_public_host(hub_settings: dict) -> str:
    """Address used in notes URLs and QR codes.

    public_host = "auto" detects the laptop's LAN address at call time.
    Any other value is used as written, so a pocket-router IP can override it.
    """
    raw = str(hub_settings.get("public_host", "auto")).strip()
    if raw.lower() != "auto":
        return raw
    return detect_lan_ip()


# The pod's own Wi-Fi (Kinig-Pod) hands out 192.168.4.x with the pod at .1.
POD_NETWORK_GATEWAY = "192.168.4.1"
POD_NETWORK_PREFIX = "192.168.4."


def detect_lan_ip() -> str:
    """Best-effort LAN address. No packet is sent and nothing is downloaded.

    Connecting a UDP socket makes the OS fill in the source address it would
    use for that destination. If this laptop is on the pod's Wi-Fi, that
    address wins, even when ethernet or another Wi-Fi holds the default route.
    Otherwise the default route's address is used. 192.0.2.1 is a
    documentation address, not a server we contact.
    """
    pod_side = source_ip_for(POD_NETWORK_GATEWAY)
    if pod_side.startswith(POD_NETWORK_PREFIX):
        return pod_side
    ip = source_ip_for("192.0.2.1")
    if ip.startswith("127."):
        log.warning(
            "No LAN address detected. Phones cannot open 127.0.0.1. "
            "Set public_host in hub/config/hub.toml to this laptop's IP."
        )
    return ip


def source_ip_for(destination: str) -> str:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect((destination, 9))
        return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        sock.close()


def _read_toml(path: Path) -> dict:
    if not path.is_file():
        raise FileNotFoundError(f"Missing config file: {path}")
    with path.open("rb") as handle:
        data = tomllib.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"{path} must be a TOML table")
    return data
