"""SQLite storage for session text.

Audio does not go in this database. The buffer holds samples in memory and
cleanup.py drops them after transcription. What remains is the transcript
and the notes, and reading those requires the session token.
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
)
"""


class Storage:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute(_SCHEMA)

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
