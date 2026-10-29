"""Pod pairing, the host console, and the routes the ESP32 polls.

Pod side (the ESP32 or scripts/fake_esp32.py):
    POST /pods/hello        {"mac": "..."} -> pairing code
    GET  /pods/{id}         what to do now (pairing, ready, record, stop, show_qr)
    GET  /pods/{id}/qr      the join QR as raw modules, so the pod needs no QR library

Host side (the /host page on the laptop):
    GET  /host, GET /host/state
    POST /host/pods/{id}/approve | start | stop | clear | forget
    POST /host/viewers/{id}/approve | deny

The pod still uploads audio and closes the session with the normal
/sessions routes. The host only flips what GET /pods/{id} returns.
"""

from __future__ import annotations

import qrcode
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, Response

from kinig_hub.api.sessions import close_session, notes_path, open_session
from kinig_hub.config import hub_dir, resolved_public_host
from kinig_hub.pods import Pod, normalize_pod_id
from kinig_hub.storage import JOIN_CODE_LENGTH

router = APIRouter()


def join_url(config, session_id: str) -> str:
    """Short on purpose: it has to fit a version 3 QR on a 128x64 OLED."""
    host = resolved_public_host(config.hub)
    port = int(config.hub["http_port"])
    return f"http://{host}:{port}/j/{session_id[:JOIN_CODE_LENGTH]}"


def qr_modules(text: str) -> list[list[bool]]:
    """Smallest QR that holds the text, low error correction, no quiet zone."""
    code = qrcode.QRCode(border=0, error_correction=qrcode.constants.ERROR_CORRECT_L)
    code.add_data(text)
    code.make(fit=True)
    return code.get_matrix()


# ---------------------------------------------------------------- pod side


@router.post("/pods/hello")
async def pod_hello(request: Request):
    hub = request.app.state.hub
    payload = await _json(request)
    pod_id = normalize_pod_id(payload.get("mac", ""))
    if pod_id is None:
        raise HTTPException(status_code=400, detail="mac is required")
    pod, orphan = hub.pods.hello(pod_id)
    if orphan:
        _close_quietly(hub, orphan)
    return {"pod_id": pod.pod_id, "code": pod.code, "paired": pod.paired}


@router.get("/pods/{pod_id}")
async def pod_state(pod_id: str, request: Request):
    hub = request.app.state.hub
    pod = hub.pods.seen(pod_id)
    if pod is None:
        # Hub restarted or the host pressed Forget. The pod says hello again.
        raise HTTPException(status_code=404, detail="unknown pod")
    return state_for(hub, pod)


@router.get("/pods/{pod_id}/qr")
async def pod_qr(pod_id: str, request: Request):
    """One byte with the side length N, then N*N bytes of 0 or 1, row by row."""
    hub = request.app.state.hub
    pod = _pod_or_404(hub, pod_id)
    if pod.session_id is None:
        raise HTTPException(status_code=409, detail="pod has no session")
    matrix = qr_modules(join_url(hub.config, pod.session_id))
    size = len(matrix)
    body = bytes([size]) + bytes(1 if on else 0 for row in matrix for on in row)
    return Response(content=body, media_type="application/octet-stream")


def state_for(hub, pod: Pod) -> dict:
    """What the pod should be doing. Flat JSON: the firmware parses it by hand."""
    base = {"pod_id": pod.pod_id, "code": pod.code}
    if not pod.paired:
        return {**base, "state": "pairing"}
    session = hub.storage.get_session(pod.session_id) if pod.session_id else None
    if session is None:
        return {**base, "state": "ready"}
    shared = {
        "session_id": session["id"],
        "join_url": join_url(hub.config, session["id"]),
        "status": session["status"],
    }
    if session["status"] != "recording":
        return {**base, **shared, "state": "show_qr"}
    if pod.stop_requested:
        return {**base, **shared, "state": "stop"}
    return {
        **base,
        **shared,
        "state": "record",
        "chunk_ms": session["chunk_ms"],
        "buffer_count": session["buffer_count"],
        "mic_shift": session["mic_shift"],
    }


# ---------------------------------------------------------------- host side


@router.get("/host", response_class=HTMLResponse)
async def host_page():
    path = hub_dir() / "kinig_hub" / "web" / "templates" / "host.html"
    return HTMLResponse(path.read_text(encoding="utf-8"))


@router.get("/host/state")
async def host_state(request: Request):
    hub = request.app.state.hub
    config = hub.config
    pods = []
    for pod in hub.pods.all():
        state = state_for(hub, pod)
        pods.append(
            {
                "pod_id": pod.pod_id,
                "label": pod.label,
                "code": pod.code,
                "paired": pod.paired,
                "online": pod.online(),
                "state": state["state"],
                "session_id": pod.session_id,
            }
        )
    sessions = []
    for session in hub.storage.recent_sessions(8):
        sessions.append(
            {
                "session_id": session["id"],
                "client": session["client"],
                "status": session["status"],
                "created_at": session["created_at"],
                "join_url": join_url(config, session["id"]),
                "notes_path": notes_path(session["id"], session["token"]),
                "viewers": [
                    {"viewer_id": v["id"], "name": v["name"], "status": v["status"]}
                    for v in hub.storage.viewers_for(session["id"])
                ],
            }
        )
    return {"pods": pods, "sessions": sessions}


@router.get("/host/qr/{session_id}.svg")
async def host_qr(session_id: str, request: Request):
    """The same join QR the pod shows, for the laptop screen."""
    hub = request.app.state.hub
    if hub.storage.get_session(session_id) is None:
        raise HTTPException(status_code=404, detail="unknown session")
    matrix = qr_modules(join_url(hub.config, session_id))
    return Response(content=_svg(matrix), media_type="image/svg+xml")


@router.post("/host/pods/{pod_id}/approve")
async def host_approve_pod(pod_id: str, request: Request):
    pod = _pod_or_404(request.app.state.hub, pod_id)
    pod.paired = True
    return {"ok": True}


@router.post("/host/pods/{pod_id}/start")
async def host_start(pod_id: str, request: Request):
    hub = request.app.state.hub
    pod = _pod_or_404(hub, pod_id)
    if not pod.paired:
        raise HTTPException(status_code=409, detail="approve the pod first")
    if pod.session_id and state_for(hub, pod)["state"] in {"record", "stop"}:
        raise HTTPException(status_code=409, detail="pod is already recording")
    payload = await _json(request)
    opened = open_session(hub, declared="esp32", mode=payload.get("mode"), remote_host=None)
    pod.session_id = opened["session_id"]
    pod.stop_requested = False
    return {"ok": True, "session_id": pod.session_id}


@router.post("/host/pods/{pod_id}/stop")
async def host_stop(pod_id: str, request: Request):
    """Ask the pod to send its last chunks and close the session itself.

    If the pod is offline, or Stop is pressed a second time, the hub closes
    the session with whatever audio it already has.
    """
    hub = request.app.state.hub
    pod = _pod_or_404(hub, pod_id)
    if pod.session_id is None:
        raise HTTPException(status_code=409, detail="pod is not recording")
    if pod.stop_requested or not pod.online():
        _close_quietly(hub, pod.session_id)
        return {"ok": True, "forced": True}
    pod.stop_requested = True
    return {"ok": True, "forced": False}


@router.post("/host/pods/{pod_id}/clear")
async def host_clear(pod_id: str, request: Request):
    """Back to ready, so the pod drops the QR and waits for the next Start."""
    hub = request.app.state.hub
    pod = _pod_or_404(hub, pod_id)
    if pod.session_id:
        _close_quietly(hub, pod.session_id)
    pod.session_id = None
    pod.stop_requested = False
    return {"ok": True}


@router.post("/host/pods/{pod_id}/forget")
async def host_forget(pod_id: str, request: Request):
    hub = request.app.state.hub
    pod = hub.pods.forget(pod_id)
    if pod is None:
        raise HTTPException(status_code=404, detail="unknown pod")
    if pod.session_id:
        _close_quietly(hub, pod.session_id)
    return {"ok": True}


@router.post("/host/viewers/{viewer_id}/{decision}")
async def host_decide_viewer(viewer_id: str, decision: str, request: Request):
    statuses = {"approve": "approved", "deny": "denied"}
    if decision not in statuses:
        raise HTTPException(status_code=404, detail="use approve or deny")
    hub = request.app.state.hub
    if not hub.storage.set_viewer_status(viewer_id, statuses[decision]):
        raise HTTPException(status_code=404, detail="unknown viewer")
    return {"ok": True}


# ---------------------------------------------------------------- helpers


def _pod_or_404(hub, pod_id: str) -> Pod:
    pod = hub.pods.get(pod_id)
    if pod is None:
        raise HTTPException(status_code=404, detail="unknown pod")
    return pod


def _close_quietly(hub, session_id: str) -> None:
    session = hub.storage.get_session(session_id)
    if session is not None and session["status"] == "recording":
        close_session(hub, session_id)


def _svg(matrix: list[list[bool]]) -> str:
    box = 8
    margin = 2
    size = (len(matrix) + margin * 2) * box
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size} {size}">',
        '<rect width="100%" height="100%" fill="#ffffff"/>',
    ]
    for y, row in enumerate(matrix):
        for x, on in enumerate(row):
            if on:
                parts.append(
                    f'<rect x="{(x + margin) * box}" y="{(y + margin) * box}" '
                    f'width="{box}" height="{box}" fill="#111111"/>'
                )
    parts.append("</svg>")
    return "".join(parts)


async def _json(request: Request) -> dict:
    try:
        payload = await request.json()
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}
