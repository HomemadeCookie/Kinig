"""One-screen demo: Start shows a live transcript, Stop shows action items.

Served whether dev_mode is on or off. The page records from the laptop mic and
uses the normal /sessions routes, the same ones the ESP32 pod uses. Open it on
the hub laptop as http://localhost:8000/demo so the browser allows the mic.
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from kinig_hub.config import hub_dir

router = APIRouter()


@router.get("/demo", response_class=HTMLResponse)
async def demo_page():
    path = hub_dir() / "kinig_hub" / "web" / "templates" / "demo.html"
    return HTMLResponse(path.read_text(encoding="utf-8"))
