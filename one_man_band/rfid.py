from __future__ import annotations

import re
import sqlite3
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


UID_RE = re.compile(r"^[0-9A-F]{2}(:[0-9A-F]{2}){7}$")


def normalize_uid(value: object) -> str:
    uid = str(value or "").strip().upper().replace("-", ":").replace(" ", ":")
    parts = [part for part in uid.split(":") if part]
    if len(parts) == 1 and len(parts[0]) == 16:
        parts = [parts[0][index : index + 2] for index in range(0, 16, 2)]
    uid = ":".join(part.zfill(2) for part in parts)
    if not UID_RE.match(uid):
        raise ValueError("RFID UID must be 8 hex bytes, for example 14:B0:30:1A:53:01:04:E0")
    return uid


def normalize_group_name(value: object) -> str:
    name = str(value or "").strip().lower()
    name = re.sub(r"[^a-z0-9]+", "_", name).strip("_")
    if not name:
        raise ValueError("RFID group name is required")
    return name


@dataclass(slots=True)
class RfidTag:
    uid: str
    label: str
    enabled: bool
    groups: list[str]
    notes: str
    created_at: float
    updated_at: float

    def to_payload(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class RfidGroup:
    name: str
    label: str
    created_at: float
    updated_at: float

    def to_payload(self) -> dict[str, Any]:
        return asdict(self)


class RfidCatalog:
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
                CREATE TABLE IF NOT EXISTS rfid_tags (
                    uid TEXT PRIMARY KEY,
                    label TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    notes TEXT NOT NULL DEFAULT '',
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS rfid_groups (
                    name TEXT PRIMARY KEY,
                    label TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS rfid_tag_groups (
                    uid TEXT NOT NULL,
                    group_name TEXT NOT NULL,
                    PRIMARY KEY (uid, group_name),
                    FOREIGN KEY(uid) REFERENCES rfid_tags(uid) ON DELETE CASCADE,
                    FOREIGN KEY(group_name) REFERENCES rfid_groups(name) ON DELETE CASCADE
                )
                """
            )

    def payload(self) -> dict[str, Any]:
        return {
            "tags": [tag.to_payload() for tag in self.list_tags()],
            "groups": [group.to_payload() for group in self.list_groups()],
        }

    def list_groups(self) -> list[RfidGroup]:
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM rfid_groups ORDER BY label ASC").fetchall()
        return [self._row_to_group(row) for row in rows]

    def list_tags(self) -> list[RfidTag]:
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM rfid_tags ORDER BY label ASC").fetchall()
            groups = self._groups_by_uid(connection)
        return [self._row_to_tag(row, groups.get(str(row["uid"]), [])) for row in rows]

    def get_tag(self, uid: object) -> RfidTag | None:
        normalized_uid = normalize_uid(uid)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM rfid_tags WHERE uid = ?",
                (normalized_uid,),
            ).fetchone()
            if row is None:
                return None
            groups = self._groups_by_uid(connection).get(normalized_uid, [])
        return self._row_to_tag(row, groups)

    def upsert_group(self, payload: dict[str, Any]) -> RfidGroup:
        name = normalize_group_name(payload.get("name") or payload.get("label"))
        label = str(payload.get("label") or name.replace("_", " ").title()).strip()
        now = time.time()
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT created_at FROM rfid_groups WHERE name = ?",
                (name,),
            ).fetchone()
            created_at = float(existing["created_at"]) if existing else now
            connection.execute(
                """
                INSERT OR REPLACE INTO rfid_groups (name, label, created_at, updated_at)
                VALUES (?, ?, ?, ?)
                """,
                (name, label, created_at, now),
            )
        return RfidGroup(name=name, label=label, created_at=created_at, updated_at=now)

    def upsert_tag(self, payload: dict[str, Any]) -> RfidTag:
        uid = normalize_uid(payload.get("uid"))
        label = str(payload.get("label") or uid).strip()
        notes = str(payload.get("notes") or "").strip()
        enabled = bool(payload.get("enabled", True))
        group_names = [
            normalize_group_name(item)
            for item in payload.get("groups", [])
            if str(item or "").strip()
        ]
        now = time.time()
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT created_at FROM rfid_tags WHERE uid = ?",
                (uid,),
            ).fetchone()
            created_at = float(existing["created_at"]) if existing else now
            connection.execute(
                """
                INSERT OR REPLACE INTO rfid_tags (uid, label, enabled, notes, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (uid, label, int(enabled), notes, created_at, now),
            )
            connection.execute("DELETE FROM rfid_tag_groups WHERE uid = ?", (uid,))
            for group_name in group_names:
                self._ensure_group(connection, group_name, now)
                connection.execute(
                    "INSERT OR IGNORE INTO rfid_tag_groups (uid, group_name) VALUES (?, ?)",
                    (uid, group_name),
                )
        return RfidTag(
            uid=uid,
            label=label,
            enabled=enabled,
            groups=group_names,
            notes=notes,
            created_at=created_at,
            updated_at=now,
        )

    def delete_tag(self, uid: object) -> None:
        normalized_uid = normalize_uid(uid)
        with self._connect() as connection:
            connection.execute("DELETE FROM rfid_tags WHERE uid = ?", (normalized_uid,))

    def delete_group(self, name: object) -> None:
        group_name = normalize_group_name(name)
        with self._connect() as connection:
            connection.execute("DELETE FROM rfid_tag_groups WHERE group_name = ?", (group_name,))
            connection.execute("DELETE FROM rfid_groups WHERE name = ?", (group_name,))

    def enrich_state(self, payload: dict[str, Any]) -> dict[str, Any]:
        uid = str(payload.get("tinyrfid_uid") or "")
        tag = self.get_tag(uid) if uid else None
        return {
            **payload,
            "tinyrfid_tag_known": bool(tag and tag.enabled),
            "tinyrfid_tag_label": tag.label if tag else "",
            "tinyrfid_groups": tag.groups if tag and tag.enabled else [],
            "tinyrfid_catalog": self.payload(),
        }

    def _ensure_group(self, connection: sqlite3.Connection, name: str, now: float) -> None:
        existing = connection.execute(
            "SELECT name FROM rfid_groups WHERE name = ?",
            (name,),
        ).fetchone()
        if existing is not None:
            return
        connection.execute(
            """
            INSERT INTO rfid_groups (name, label, created_at, updated_at)
            VALUES (?, ?, ?, ?)
            """,
            (name, name.replace("_", " ").title(), now, now),
        )

    def _groups_by_uid(self, connection: sqlite3.Connection) -> dict[str, list[str]]:
        rows = connection.execute(
            "SELECT uid, group_name FROM rfid_tag_groups ORDER BY group_name ASC"
        ).fetchall()
        groups: dict[str, list[str]] = {}
        for row in rows:
            groups.setdefault(str(row["uid"]), []).append(str(row["group_name"]))
        return groups

    def _row_to_tag(self, row: sqlite3.Row, groups: list[str]) -> RfidTag:
        return RfidTag(
            uid=str(row["uid"]),
            label=str(row["label"]),
            enabled=bool(row["enabled"]),
            groups=groups,
            notes=str(row["notes"]),
            created_at=float(row["created_at"]),
            updated_at=float(row["updated_at"]),
        )

    def _row_to_group(self, row: sqlite3.Row) -> RfidGroup:
        return RfidGroup(
            name=str(row["name"]),
            label=str(row["label"]),
            created_at=float(row["created_at"]),
            updated_at=float(row["updated_at"]),
        )
