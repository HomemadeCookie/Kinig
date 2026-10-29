"""Join QR -> name -> wait for the host -> notes.

    GET  /j/{code}          join page (code = first 8 characters of the session id)
    POST /j/{code}          form field "name"; creates a pending viewer
    GET  /w/{viewer_id}     waiting page; polls the route below
    GET  /w/{viewer_id}/state  {"status": ..., "notes_path": only once approved}

The notes page itself is unchanged. Approval only decides whether the viewer
is given its link.
"""

from __future__ import annotations

import html

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from kinig_hub.api.sessions import notes_path
from kinig_hub.config import hub_dir

router = APIRouter()

NAME_LIMIT = 40


@router.get("/j/{code}", response_class=HTMLResponse)
async def join_page(code: str, request: Request):
    session = _session_or_404(request.app.state.hub, code)
    return HTMLResponse(_render("join.html", {"[[code]]": html.escape(session["id"][:8])}))


@router.post("/j/{code}")
async def join(code: str, request: Request, name: str = Form("")):
    hub = request.app.state.hub
    session = _session_or_404(hub, code)
    cleaned = " ".join(name.split())[:NAME_LIMIT] or "Guest"
    viewer = hub.storage.create_viewer(session["id"], cleaned)
    return RedirectResponse(f"/w/{viewer['id']}", status_code=303)


@router.get("/w/{viewer_id}", response_class=HTMLResponse)
async def wait_page(viewer_id: str, request: Request):
    viewer = _viewer_or_404(request.app.state.hub, viewer_id)
    replacements = {
        "[[name]]": html.escape(viewer["name"]),
        "[[state_url]]": html.escape(f"/w/{viewer['id']}/state", quote=True),
    }
    return HTMLResponse(_render("wait.html", replacements))


@router.get("/w/{viewer_id}/state")
async def wait_state(viewer_id: str, request: Request):
    hub = request.app.state.hub
    viewer = _viewer_or_404(hub, viewer_id)
    body = {"status": viewer["status"]}
    if viewer["status"] == "approved":
        session = hub.storage.get_session(viewer["session_id"])
        if session is not None:
            body["notes_path"] = notes_path(session["id"], session["token"])
    return body


def _session_or_404(hub, code: str) -> dict:
    session = hub.storage.find_by_join_code(code)
    if session is None:
        raise HTTPException(status_code=404, detail="this QR is not from a current session")
    return session


def _viewer_or_404(hub, viewer_id: str) -> dict:
    viewer = hub.storage.get_viewer(viewer_id)
    if viewer is None:
        raise HTTPException(status_code=404, detail="unknown viewer")
    return viewer


def _render(template: str, replacements: dict[str, str]) -> str:
    page = (hub_dir() / "kinig_hub" / "web" / "templates" / template).read_text(encoding="utf-8")
    for marker, value in replacements.items():
        page = page.replace(marker, value)
    return page
