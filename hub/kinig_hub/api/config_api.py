"""GET and PUT /config.

Pods still read chunk_ms from POST /sessions. This route is how a person
changes the files those sessions are copied from. Pod settings apply to the
next recording. A model change is handed to the worker, which loads it
between jobs. Restart is not required for either.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, Request

from kinig_hub.config import load_config, resolved_public_host
from kinig_hub.models.registry import build_summarizer, build_transcriber
from kinig_hub.pipeline.worker import ReloadJob

router = APIRouter()

_HUB_KEYS = {"public_host", "http_port", "https_port", "dev_mode", "dev_https", "session_mode"}
_POD_KEYS = {"chunk_ms", "buffer_count", "window_s", "mic_shift", "debug_save_audio"}


@router.get("/config")
async def read_config(request: Request):
    hub = request.app.state.hub
    config = hub.reload_config()
    return {
        "hub": config.hub,
        "pod": config.pod,
        "models": config.models,
        "public_host_resolved": resolved_public_host(config.hub),
    }


@router.put("/config")
async def write_config(request: Request):
    hub = request.app.state.hub
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="JSON body must be an object")
    unknown = set(body) - {"hub", "pod", "models"}
    if unknown:
        raise HTTPException(status_code=400, detail=f"unknown section: {sorted(unknown)}")

    current = hub.reload_config()
    hub_updates = _check_hub(body.get("hub") or {})
    pod_updates = _check_pod(body.get("pod") or {})
    model_updates = _check_models(body.get("models") or {})

    folder = hub.config_folder
    staged = []
    if hub_updates:
        staged.append((folder / "hub.toml", {("", key): value for key, value in hub_updates.items()}))
    if pod_updates:
        staged.append((folder / "pod.toml", {("", key): value for key, value in pod_updates.items()}))
    if model_updates:
        staged.append((folder / "models.toml", model_updates))
    # Build every file first so a bad key does not leave a half-written config.
    rendered = [(path, _apply_assignments(path.read_text(encoding="utf-8"), updates)) for path, updates in staged]
    for path, text in rendered:
        path.write_text(text, encoding="utf-8")

    hub.config = load_config(folder)
    if model_updates and _models_changed(current.models, hub.config.models):
        try:
            transcriber = build_transcriber(hub.config.models)
            summarizer = build_summarizer(hub.config.models)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        hub.worker.submit_reload(
            ReloadJob(
                transcriber,
                summarizer,
                hub.config.models["transcriber"]["backend"],
                hub.config.models["summarizer"]["backend"],
            )
        )
    return {
        "hub": hub.config.hub,
        "pod": hub.config.pod,
        "models": hub.config.models,
        "public_host_resolved": resolved_public_host(hub.config.hub),
    }


def _check_hub(updates: dict) -> dict:
    if not isinstance(updates, dict):
        raise HTTPException(status_code=400, detail="hub must be an object")
    extra = set(updates) - _HUB_KEYS
    if extra:
        raise HTTPException(status_code=400, detail=f"unknown hub key: {sorted(extra)}")
    cleaned = {}
    if "public_host" in updates:
        host = str(updates["public_host"]).strip()
        if not host:
            raise HTTPException(status_code=400, detail="public_host cannot be empty")
        cleaned["public_host"] = host
    if "session_mode" in updates:
        mode = str(updates["session_mode"])
        if mode not in {"end", "live"}:
            raise HTTPException(status_code=400, detail="session_mode must be 'end' or 'live'")
        cleaned["session_mode"] = mode
    for key in ("dev_mode", "dev_https"):
        if key in updates:
            cleaned[key] = _as_bool(updates[key], key)
    for key in ("http_port", "https_port"):
        if key in updates:
            cleaned[key] = _as_int(updates[key], key, 1, 65535)
    return cleaned


def _check_pod(updates: dict) -> dict:
    if not isinstance(updates, dict):
        raise HTTPException(status_code=400, detail="pod must be an object")
    extra = set(updates) - _POD_KEYS
    if extra:
        raise HTTPException(status_code=400, detail=f"unknown pod key: {sorted(extra)}")
    cleaned = {}
    if "chunk_ms" in updates:
        cleaned["chunk_ms"] = _as_int(updates["chunk_ms"], "chunk_ms", 100, 5000)
    if "buffer_count" in updates:
        cleaned["buffer_count"] = _as_int(updates["buffer_count"], "buffer_count", 1, 16)
    if "window_s" in updates:
        value = updates["window_s"]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise HTTPException(status_code=400, detail="window_s must be a number")
        if not 1 <= float(value) <= 60:
            raise HTTPException(status_code=400, detail="window_s must be from 1 to 60")
        cleaned["window_s"] = float(value)
    if "mic_shift" in updates:
        cleaned["mic_shift"] = _as_int(updates["mic_shift"], "mic_shift", 0, 31)
    if "debug_save_audio" in updates:
        cleaned["debug_save_audio"] = _as_bool(updates["debug_save_audio"], "debug_save_audio")
    return cleaned


def _check_models(updates: dict) -> dict:
    """Flatten a models object into (section, key) assignments."""
    if not isinstance(updates, dict):
        raise HTTPException(status_code=400, detail="models must be an object")
    extra = set(updates) - {"transcriber", "summarizer"}
    if extra:
        raise HTTPException(status_code=400, detail=f"unknown models key: {sorted(extra)}")
    flat: dict[tuple[str, str], object] = {}
    for section in ("transcriber", "summarizer"):
        block = updates.get(section)
        if block is None:
            continue
        if not isinstance(block, dict):
            raise HTTPException(status_code=400, detail=f"{section} must be an object")
        if "backend" in block:
            flat[(section, "backend")] = str(block["backend"])
        for key in ("fallback", "timeout_s", "prompt_file", "output_language"):
            if key in block:
                flat[(section, key)] = block[key]
        options = block.get("options") or {}
        if not isinstance(options, dict):
            raise HTTPException(status_code=400, detail=f"{section}.options must be an object")
        for key, value in options.items():
            flat[(f"{section}.options", str(key))] = value
    return flat


def _models_changed(before: dict, after: dict) -> bool:
    return json.dumps(before, sort_keys=True) != json.dumps(after, sort_keys=True)


def _as_bool(value, name: str) -> bool:
    if not isinstance(value, bool):
        raise HTTPException(status_code=400, detail=f"{name} must be true or false")
    return value


def _as_int(value, name: str, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise HTTPException(status_code=400, detail=f"{name} must be an integer")
    if not low <= value <= high:
        raise HTTPException(status_code=400, detail=f"{name} must be from {low} to {high}")
    return value


def _apply_assignments(text: str, updates: dict[tuple[str, str], object]) -> str:
    """Replace key = value lines. Comments on the same line stay."""
    section = ""
    lines = []
    seen: set[tuple[str, str]] = set()
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]") and "=" not in stripped:
            section = stripped[1:-1].strip()
            lines.append(line)
            continue
        if "=" in line and not stripped.startswith("#"):
            key = line.split("=", 1)[0].strip()
            token = (section, key)
            if token in updates:
                lines.append(_replace_value(line, key, updates[token]))
                seen.add(token)
                continue
        lines.append(line)
    missing = [f"{section}.{key}" if section else key for section, key in updates if (section, key) not in seen]
    if missing:
        raise HTTPException(status_code=400, detail=f"config has no line for: {missing}")
    return "\n".join(lines) + "\n"


def _replace_value(line: str, key: str, value: object) -> str:
    left, right = line.split("=", 1)
    comment = ""
    if "#" in right:
        _, comment = right.split("#", 1)
        comment = "  #" + comment.rstrip()
    indent = line[: len(line) - len(line.lstrip())]
    return f"{indent}{key} = {_toml(value)}{comment}"


def _toml(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return json.dumps(value)
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)
