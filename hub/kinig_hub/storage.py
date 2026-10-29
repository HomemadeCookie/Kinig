"""SQLite storage for session text.

Audio does not go in this database. The buffer holds samples in memory and
cleanup.py drops them after transcription. What remains is the transcript
and the notes, and reading those requires the session token.

A viewer is a phone that scanned the join QR and typed a name. The host
approves it on /host, and only then does the viewer get the notes link.
"""

from __future__ import annotations

import json
import secrets
import sqlite3
import threading
import uuid
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    token TEXT NOT NULL,
    client TEXT NOT NULL,
    mode TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    transcript TEXT NOT NULL DEFAULT '',
    summary TEXT NOT NULL DEFAULT '',
    key_points TEXT NOT NULL DEFAULT '[]',
    action_items TEXT NOT NULL DEFAULT '[]',
    error TEXT NOT NULL DEFAULT '',
    chunk_ms INTEGER NOT NULL,
    buffer_count INTEGER NOT NULL,
    window_s REAL NOT NULL,
    mic_shift INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS viewers (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
)
"""

# The join QR carries this many characters of the session id. Short enough for
# a version 3 QR on the 128x64 OLED, long enough that two sessions never share it.
JOIN_CODE_LENGTH = 8
VIEWER_STATUSES = {"pending", "approved", "denied"}


class Storage:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    def create_session(
        self,
        *,
        client: str,
        mode: str,
        chunk_ms: int,
        buffer_count: int,
        window_s: float,
        mic_shift: int,
    ) -> dict:
        session_id = uuid.uuid4().hex
        token = secrets.token_urlsafe(24)
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO sessions (
                    id, token, client, mode, status,
                    chunk_ms, buffer_count, window_s, mic_shift
                ) VALUES (?, ?, ?, ?, 'recording', ?, ?, ?, ?)
                """,
                (session_id, token, client, mode, chunk_ms, buffer_count, window_s, mic_shift),
            )
        session = self.get_session(session_id)
        assert session is not None
        return session

    def get_session(self, session_id: str) -> dict | None:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
        if row is None:
            return None
        return _row_to_dict(row)

    def set_status(self, session_id: str, status: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("UPDATE sessions SET status = ? WHERE id = ?", (status, session_id))

    def append_transcript(self, session_id: str, text: str) -> None:
        cleaned = text.strip()
        if not cleaned:
            return
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT transcript FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
            if row is None:
                return
            current = row["transcript"]
            combined = f"{current} {cleaned}".strip() if current else cleaned
            conn.execute(
                "UPDATE sessions SET transcript = ? WHERE id = ?",
                (combined, session_id),
            )

    def save_notes(self, session_id: str, notes: dict) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                UPDATE sessions
                SET summary = ?, key_points = ?, action_items = ?
                WHERE id = ?
                """,
                (
                    notes["summary"],
                    json.dumps(notes["key_points"]),
                    json.dumps(notes["action_items"]),
                    session_id,
                ),
            )

    def set_error(self, session_id: str, message: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("UPDATE sessions SET error = ? WHERE id = ?", (message, session_id))

    def find_by_join_code(self, code: str) -> dict | None:
        if len(code) != JOIN_CODE_LENGTH or not code.isalnum():
            return None
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM sessions WHERE substr(id, 1, ?) = ? ORDER BY created_at DESC",
                (JOIN_CODE_LENGTH, code.lower()),
            ).fetchone()
        if row is None:
            return None
        return _row_to_dict(row)

    def recent_sessions(self, limit: int = 10) -> list[dict]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM sessions ORDER BY created_at DESC, rowid DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [_row_to_dict(row) for row in rows]

    def create_viewer(self, session_id: str, name: str) -> dict:
        viewer_id = secrets.token_urlsafe(12)
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO viewers (id, session_id, name) VALUES (?, ?, ?)",
                (viewer_id, session_id, name),
            )
        viewer = self.get_viewer(viewer_id)
        assert viewer is not None
        return viewer

    def get_viewer(self, viewer_id: str) -> dict | None:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT * FROM viewers WHERE id = ?", (viewer_id,)).fetchone()
        return dict(row) if row is not None else None

    def set_viewer_status(self, viewer_id: str, status: str) -> bool:
        if status not in VIEWER_STATUSES:
            raise ValueError(f"viewer status must be one of {sorted(VIEWER_STATUSES)}")
        with self._lock, self._connect() as conn:
            cursor = conn.execute(
                "UPDATE viewers SET status = ? WHERE id = ?", (status, viewer_id)
            )
        return cursor.rowcount > 0

    def viewers_for(self, session_id: str) -> list[dict]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM viewers WHERE session_id = ? ORDER BY created_at, rowid",
                (session_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def token_matches(self, session: dict, token: str) -> bool:
        expected = session["token"]
        if len(expected) != len(token):
            return False
        return secrets.compare_digest(expected, token)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn


def _row_to_dict(row: sqlite3.Row) -> dict:
    data = dict(row)
    data["key_points"] = json.loads(data["key_points"])
    data["action_items"] = json.loads(data["action_items"])
    return data
