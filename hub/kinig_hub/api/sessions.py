"""Session, audio, stop, and notes routes.

POST /sessions/{id}/audio stores the chunk and returns immediately.
Transcription runs on the single worker thread, never on this request.

Pod settings in the start response are copied from pod.toml at request time.
Callers must use those values. chunk_ms is not chosen here.
"""

from __future__ import annotations

import html
import json
import time
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse

from kinig_hub.audio.buffer import AudioBuffer
from kinig_hub.config import hub_dir, resolved_public_host

router = APIRouter()

_LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost"}


def notes_url(config, session_id: str, token: str) -> str:
    """Always HTTP on the hub port. Never the request host, never localhost.

    public_host "auto" is resolved here. A manual IP in hub.toml is kept as written.
    """
    host = resolved_public_host(config.hub)
    port = int(config.hub["http_port"])
    return f"http://{host}:{port}/s/{session_id}?t={quote(token, safe='')}"


def refine_client(declared: str, host: str | None) -> str:
    """browser becomes browser-laptop or browser-phone based on who connected."""
    if declared != "browser":
        return declared
    if host in _LOCAL_HOSTS:
        return "browser-laptop"
    return "browser-phone"


def pod_settings(pod: dict) -> dict:
    return {
        "chunk_ms": pod["chunk_ms"],
        "buffer_count": pod["buffer_count"],
        "window_s": pod["window_s"],
        "mic_shift": pod["mic_shift"],
        "debug_save_audio": bool(pod.get("debug_save_audio", False)),
    }


@router.post("/sessions")
async def start_session(request: Request):
    hub = request.app.state.hub
    payload = await _json_object(request)
    host = request.client.host if request.client else None
    return open_session(
        hub,
        declared=str(payload.get("client") or "unknown"),
        mode=payload.get("mode"),
        remote_host=host,
    )


def open_session(hub, *, declared: str, mode: str | None, remote_host: str | None) -> dict:
    """Create a session and return the settings the caller must use."""
    config = hub.reload_config()
    client = refine_client(declared, remote_host)
    chosen = str(mode or config.hub.get("session_mode") or "end")
    if chosen not in {"end", "live"}:
        raise HTTPException(status_code=400, detail="mode must be 'end' or 'live'")

    settings = pod_settings(config.pod)
    session = hub.storage.create_session(
        client=client,
        mode=chosen,
        chunk_ms=int(settings["chunk_ms"]),
        buffer_count=int(settings["buffer_count"]),
        window_s=float(settings["window_s"]),
        mic_shift=int(settings["mic_shift"]),
    )
    buffer = AudioBuffer(
        window_s=float(settings["window_s"]),
        chunk_ms=int(settings["chunk_ms"]),
        keep_debug_copy=settings["debug_save_audio"],
    )
    with hub.buffer_lock:
        hub.buffers[session["id"]] = buffer
    return {
        "session_id": session["id"],
        "token": session["token"],
        "notes_url": notes_url(config, session["id"], session["token"]),
        "client": client,
        "mode": chosen,
        **settings,
    }


@router.post("/sessions/{session_id}/audio")
async def upload_audio(session_id: str, request: Request, seq: int = Query(...)):
    """Store one PCM chunk and return. Do not transcribe here."""
    if seq < 0:
        raise HTTPException(status_code=400, detail="seq starts at 0")
    hub = request.app.state.hub
    started = time.perf_counter()
    pcm = await request.body()
    try:
        windows = _accept_chunk(hub, session_id, seq, pcm, started)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    for audio in windows:
        hub.worker.submit_transcribe(session_id, audio)
    return {"ok": True}


@router.post("/sessions/{session_id}/stop")
async def stop_session(session_id: str, request: Request):
    return close_session(request.app.state.hub, session_id)


def close_session(hub, session_id: str) -> dict:
    session = _require_session(hub, session_id)
    with hub.buffer_lock:
        if session_id in hub.stopping:
            windows = []
            already = True
        else:
            hub.stopping.add(session_id)
            buffer = hub.buffers.get(session_id)
            windows = buffer.pop_windows(final=True) if buffer is not None else []
            already = False
    if not already:
        hub.storage.set_status(session_id, "transcribing")
        for audio in windows:
            hub.worker.submit_transcribe(session_id, audio)
        hub.worker.submit_finalize(session_id)
    current = hub.storage.get_session(session_id)
    return {
        "session_id": session_id,
        "status": current["status"] if current else session["status"],
        "notes_url": notes_url(hub.config, session_id, session["token"]),
    }


def queue_chunk(hub, session_id: str, seq: int, pcm: bytes) -> None:
    """Same path as POST /audio. Used by the dev file upload."""
    started = time.perf_counter()
    windows = _accept_chunk(hub, session_id, seq, pcm, started)
    for audio in windows:
        hub.worker.submit_transcribe(session_id, audio)


@router.get("/s/{session_id}/transcript")
async def read_transcript(session_id: str, request: Request, t: str = Query("")):
    hub = request.app.state.hub
    session = _authorized_session(hub, session_id, t)
    return JSONResponse(_public_notes(session))


@router.get("/s/{session_id}", response_class=HTMLResponse)
async def read_notes(session_id: str, request: Request, t: str = Query("")):
    hub = request.app.state.hub
    session = _authorized_session(hub, session_id, t)
    page = _render_notes(session, t)
    return HTMLResponse(page)


def _accept_chunk(hub, session_id: str, seq: int, pcm: bytes, started: float) -> list:
    # Storage is read outside buffer_lock. The worker takes the buffer lock
    # only after it has finished its database calls, so the two locks are
    # never held together.
    session = hub.storage.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="unknown session")
    if session["status"] != "recording":
        raise _conflict()
    with hub.buffer_lock:
        if session_id in hub.stopping:
            raise _conflict()
        buffer = hub.buffers.get(session_id)
        if buffer is None:
            raise HTTPException(status_code=404, detail="unknown session")
        upload_ms = (time.perf_counter() - started) * 1000
        buffer.add_chunk(seq, pcm, upload_ms)
        if session["mode"] != "live":
            return []
        return buffer.pop_windows(final=False)


def _conflict() -> HTTPException:
    return HTTPException(status_code=409, detail="session is no longer recording")


def _require_session(hub, session_id: str) -> dict:
    session = hub.storage.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="unknown session")
    return session


def _authorized_session(hub, session_id: str, token: str) -> dict:
    session = _require_session(hub, session_id)
    if not token or not hub.storage.token_matches(session, token):
        raise HTTPException(status_code=403, detail="bad or missing token")
    return session


def _public_notes(session: dict) -> dict:
    return {
        "status": session["status"],
        "transcript": session["transcript"],
        "summary": session["summary"],
        "key_points": session["key_points"],
        "action_items": session["action_items"],
        "error": session["error"],
    }


def _render_notes(session: dict, token: str) -> str:
    path = hub_dir() / "kinig_hub" / "web" / "templates" / "notes.html"
    page = path.read_text(encoding="utf-8")
    poll = f"/s/{session['id']}/transcript?t={quote(token, safe='')}"
    replacements = {
        "[[status]]": html.escape(session["status"]),
        "[[error]]": html.escape(session["error"]),
        "[[summary]]": html.escape(session["summary"]),
        "[[transcript]]": html.escape(session["transcript"]),
        "[[key_points]]": _list_items(session["key_points"]),
        "[[action_items]]": _list_items(session["action_items"]),
        "[[poll_url]]": html.escape(poll, quote=True),
    }
    for marker, value in replacements.items():
        page = page.replace(marker, value)
    return page


def _list_items(items: list) -> str:
    if not items:
        return "<li>(none)</li>"
    return "".join(f"<li>{html.escape(str(item))}</li>" for item in items)


async def _json_object(request: Request) -> dict:
    raw = await request.body()
    if not raw:
        return {}
    if "application/json" not in request.headers.get("content-type", ""):
        return {}
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="body is not JSON") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="JSON body must be an object")
    return payload
