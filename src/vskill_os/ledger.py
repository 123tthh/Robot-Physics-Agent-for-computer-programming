"""Append-only SQLite event ledger for reproducible task execution."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4


@dataclass(frozen=True, slots=True)
class LedgerEvent:
    event_id: str
    task_id: str
    call_id: str | None
    kind: str
    payload: dict[str, Any]
    created_at: str


class EventLedger:
    def __init__(self, path: str | Path = ":memory:") -> None:
        self.path = str(path)
        self._connection = sqlite3.connect(self.path)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._connection.execute("PRAGMA journal_mode = WAL")
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS events (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT NOT NULL UNIQUE,
                task_id TEXT NOT NULL,
                call_id TEXT,
                kind TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        self._connection.commit()

    def append(
        self,
        *,
        task_id: str,
        kind: str,
        payload: Mapping[str, Any],
        call_id: str | None = None,
    ) -> LedgerEvent:
        event = LedgerEvent(
            event_id=uuid4().hex,
            task_id=task_id,
            call_id=call_id,
            kind=kind,
            payload=dict(payload),
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        self._connection.execute(
            """
            INSERT INTO events(event_id, task_id, call_id, kind, payload_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                event.event_id,
                event.task_id,
                event.call_id,
                event.kind,
                json.dumps(event.payload, ensure_ascii=False, sort_keys=True),
                event.created_at,
            ),
        )
        self._connection.commit()
        return event

    def list_events(self, task_id: str) -> list[LedgerEvent]:
        rows = self._connection.execute(
            """
            SELECT event_id, task_id, call_id, kind, payload_json, created_at
            FROM events WHERE task_id = ? ORDER BY sequence ASC
            """,
            (task_id,),
        ).fetchall()
        return [
            LedgerEvent(
                event_id=row["event_id"],
                task_id=row["task_id"],
                call_id=row["call_id"],
                kind=row["kind"],
                payload=json.loads(row["payload_json"]),
                created_at=row["created_at"],
            )
            for row in rows
        ]

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> "EventLedger":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
