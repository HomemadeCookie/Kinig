"""Laptop and phone test pod. Refuses every request when dev_mode is false.

The browser is a caller. It posts the same audio chunks as the ESP32.
A file upload is decoded here and pushed into a normal session, then deleted.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import qrcode
from fastapi import APIRouter, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, Response

from kinig_hub.api.sessions import close_session, notes_url, open_session, queue_chunk
from kinig_hub.audio.decode import file_to_pcm_bytes
from kinig_hub.audio.format import BYTES_PER_SECOND, SAMPLE_RATE, SAMPLE_WIDTH_BYTES
from kinig_hub.config import hub_dir

router = APIRouter()


def _hub_or_404(request: Request):
    hub = request.app.state.hub
    config = hub.reload_config()
    if not config.hub.get("dev_mode"):
        raise HTTPException(status_code=404, detail="dev mode is off")
    return hub


@router.get("/dev", response_class=HTMLResponse)
async def dev_page(request: Request):
    _hub_or_404(request)
    path = hub_dir() / "kinig_hub" / "web" / "templates" / "dev.html"
    return HTMLResponse(path.read_text(encoding="utf-8"))


@router.get("/dev/qr/{session_id}.svg")
async def dev_qr(session_id: str, request: Request, t: str = Query("")):
    hub = _hub_or_404(request)
    session = hub.storage.get_session(session_id)
    if session is None or not hub.storage.token_matches(session, t):
        raise HTTPException(status_code=403, detail="bad or missing token")
    url = notes_url(hub.config, session_id, session["token"])
    return Response(content=qr_svg(url), media_type="image/svg+xml")


@router.get("/dev/status/{session_id}")
async def dev_status(session_id: str, request: Request, t: str = Query("")):
    hub = _hub_or_404(request)
    session = hub.storage.get_session(session_id)
    if session is None or not hub.storage.token_matches(session, t):
        raise HTTPException(status_code=403, detail="bad or missing token")
    with hub.buffer_lock:
        buffer = hub.buffers.get(session_id)
        chunks = buffer.chunks_received if buffer is not None else 0
        gaps = list(buffer.gaps) if buffer is not None else []
    snap = hub.worker.snapshot(session_id)
    models = hub.config.models
    return {
        "status": session["status"],
        "client": session["client"],
        "chunks": chunks,
        "gaps": gaps,
        "lag_seconds": snap["lag_seconds"],
        "transcriber": _model_label(models.get("transcriber") or {}, snap["transcriber"]),
        "summarizer": _model_label(models.get("summarizer") or {}, snap["summarizer"]),
        "load_error": snap["load_error"],
    }


@router.post("/dev/upload")
async def dev_upload(
    request: Request,
    file: UploadFile = File(...),
    mode: str = Form("end"),
):
    """Decode an uploaded file, feed it as normal chunks, then delete the file."""
    hub = _hub_or_404(request)
    suffix = Path(file.filename or "upload").suffix or ".bin"
    handle = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    tmp = Path(handle.name)
    try:
        handle.write(await file.read())
        handle.close()
        pcm = file_to_pcm_bytes(tmp)
    finally:
        tmp.unlink(missing_ok=True)
    host = request.client.host if request.client else None
    opened = open_session(hub, declared="browser", mode=mode, remote_host=host)
    chunk_ms = int(opened["chunk_ms"])
    width = _chunk_bytes(chunk_ms)
    seq = 0
    for start in range(0, len(pcm), width):
        piece = pcm[start : start + width]
        if len(piece) < width:
            piece = piece + bytes(width - len(piece))
        queue_chunk(hub, opened["session_id"], seq, piece)
        seq += 1
    if seq == 0:
        raise HTTPException(status_code=400, detail="file had no audio")
    closed = close_session(hub, opened["session_id"])
    opened["status"] = closed["status"]
    return opened


def _chunk_bytes(chunk_ms: int) -> int:
    """Bytes for one chunk, using the session's chunk_ms and the audio contract."""
    samples = int(round(chunk_ms * SAMPLE_RATE / 1000))
    width = samples * SAMPLE_WIDTH_BYTES
    if width <= 0 or width % SAMPLE_WIDTH_BYTES:
        raise HTTPException(status_code=400, detail="chunk_ms is not a usable size")
    # BYTES_PER_SECOND is the contract (32000). width is chunk_ms of that.
    if width > BYTES_PER_SECOND * 10:
        raise HTTPException(status_code=400, detail="chunk_ms is too large")
    return width


def _model_label(section: dict, backend: str) -> str:
    options = section.get("options") or {}
    name = options.get("model") or ""
    if name:
        return f"{backend} / {name}"
    return backend


def qr_svg(text: str) -> str:
    """SVG QR with no extra image library. The page can show it offline."""
    code = qrcode.QRCode(border=2)
    code.add_data(text)
    code.make(fit=True)
    matrix = code.get_matrix()
    box = 8
    size = len(matrix) * box
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size} {size}" ',
        f'width="{size}" height="{size}">',
        f'<rect width="100%" height="100%" fill="#ffffff"/>',
    ]
    for y, row in enumerate(matrix):
        for x, on in enumerate(row):
            if on:
                parts.append(f'<rect x="{x * box}" y="{y * box}" width="{box}" height="{box}" fill="#111111"/>')
    parts.append("</svg>")
    return "".join(parts)
