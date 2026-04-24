from __future__ import annotations

import json
import re
import sqlite3
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


ANIMATION_NAME_RE = re.compile(r"^[A-Za-z0-9 _.-]+$")


@dataclass(slots=True)
class Animation:
    id: int
    name: str
    published: bool
    duration_ms: int
    timeline: dict[str, Any]
    created_at: float
    updated_at: float

    def to_payload(self) -> dict[str, Any]:
        return asdict(self)


class AnimationStore:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._path)
        connection.row_factory = sqlite3.Row
        return connection

    def _ensure_schema(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS animations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    published INTEGER NOT NULL DEFAULT 0,
                    duration_ms INTEGER NOT NULL DEFAULT 5000,
                    timeline_json TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                )
                """
            )

    def list_animations(self) -> list[Animation]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM animations ORDER BY updated_at DESC, id DESC"
            ).fetchall()
        return [self._row_to_animation(row) for row in rows]

    def upsert_animation(self, payload: dict[str, Any]) -> Animation:
        now = time.time()
        name = self._name(payload.get("name") or "New animation")
        published = bool(payload.get("published", False))
        duration_ms = max(100, int(payload.get("duration_ms", 5000)))
        timeline = self._timeline(payload.get("timeline"))
        animation_id = payload.get("id")

        with self._connect() as connection:
            if animation_id:
                connection.execute(
                    """
                    UPDATE animations
                    SET name = ?, published = ?, duration_ms = ?, timeline_json = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        name,
                        int(published),
                        duration_ms,
                        json.dumps(timeline, sort_keys=True),
                        now,
                        int(animation_id),
                    ),
                )
                row = connection.execute(
                    "SELECT * FROM animations WHERE id = ?", (int(animation_id),)
                ).fetchone()
                if row is None:
                    raise ValueError(f"Animation not found: {animation_id}")
                return self._row_to_animation(row)

            cursor = connection.execute(
                """
                INSERT INTO animations
                    (name, published, duration_ms, timeline_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    name,
                    int(published),
                    duration_ms,
                    json.dumps(timeline, sort_keys=True),
                    now,
                    now,
                ),
            )
            row = connection.execute(
                "SELECT * FROM animations WHERE id = ?", (cursor.lastrowid,)
            ).fetchone()
        return self._row_to_animation(row)

    def delete_animation(self, animation_id: int) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM animations WHERE id = ?", (animation_id,))

    def _row_to_animation(self, row: sqlite3.Row) -> Animation:
        return Animation(
            id=int(row["id"]),
            name=str(row["name"]),
            published=bool(row["published"]),
            duration_ms=int(row["duration_ms"]),
            timeline=json.loads(str(row["timeline_json"])),
            created_at=float(row["created_at"]),
            updated_at=float(row["updated_at"]),
        )

    def _name(self, value: Any) -> str:
        name = str(value or "").strip() or "New animation"
        if not ANIMATION_NAME_RE.match(name):
            raise ValueError("Animation names can use letters, numbers, spaces, dots, dashes, and underscores")
        return name[:80]

    def _timeline(self, value: Any) -> dict[str, Any]:
        if value is None:
            return {"tracks": []}
        if not isinstance(value, dict):
            raise ValueError("Animation timeline must be an object")
        tracks = value.get("tracks", [])
        if not isinstance(tracks, list):
            raise ValueError("Animation tracks must be a list")
        return {"tracks": [track for track in tracks if isinstance(track, dict)]}
