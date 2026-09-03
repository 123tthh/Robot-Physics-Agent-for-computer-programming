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


@dataclass(frozen=True, slots=True)
class TaskCheckpoint:
    checkpoint_id: str
    task_id: str
    state: dict[str, Any]
    completed_nodes: tuple[str, ...]
    created_at: str


@dataclass(frozen=True, slots=True)
class RecoveryRecord:
    memory_id: str
    failed_skill: str
    failure_code: str
    recovery_skill: str
    outcome: str
    context: dict[str, Any]
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
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS checkpoints (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                checkpoint_id TEXT NOT NULL UNIQUE,
                task_id TEXT NOT NULL,
                state_json TEXT NOT NULL,
                completed_nodes_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS recovery_memory (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                memory_id TEXT NOT NULL UNIQUE,
                failed_skill TEXT NOT NULL,
                failure_code TEXT NOT NULL,
                recovery_skill TEXT NOT NULL,
                outcome TEXT NOT NULL,
                context_json TEXT NOT NULL,
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

    def save_checkpoint(
        self,
        *,
        task_id: str,
        state: Mapping[str, Any],
        completed_nodes: tuple[str, ...] | list[str] | set[str],
    ) -> TaskCheckpoint:
        checkpoint = TaskCheckpoint(
            checkpoint_id=uuid4().hex,
            task_id=task_id,
            state=dict(state),
            completed_nodes=tuple(sorted(completed_nodes)),
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        self._connection.execute(
            """
            INSERT INTO checkpoints(
                checkpoint_id, task_id, state_json, completed_nodes_json, created_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                checkpoint.checkpoint_id,
                checkpoint.task_id,
                json.dumps(checkpoint.state, ensure_ascii=False, sort_keys=True, default=str),
                json.dumps(checkpoint.completed_nodes),
                checkpoint.created_at,
            ),
        )
        self._connection.commit()
        return checkpoint

    def load_latest_checkpoint(self, task_id: str) -> TaskCheckpoint | None:
        row = self._connection.execute(
            """
            SELECT checkpoint_id, task_id, state_json, completed_nodes_json, created_at
            FROM checkpoints WHERE task_id = ? ORDER BY sequence DESC LIMIT 1
            """,
            (task_id,),
        ).fetchone()
        if row is None:
            return None
        return TaskCheckpoint(
            checkpoint_id=row["checkpoint_id"],
            task_id=row["task_id"],
            state=json.loads(row["state_json"]),
            completed_nodes=tuple(json.loads(row["completed_nodes_json"])),
            created_at=row["created_at"],
        )

    def remember_recovery(
        self,
        *,
        failed_skill: str,
        failure_code: str,
        recovery_skill: str,
        outcome: str,
        context: Mapping[str, Any] | None = None,
    ) -> RecoveryRecord:
        record = RecoveryRecord(
            memory_id=uuid4().hex,
            failed_skill=failed_skill,
            failure_code=failure_code,
            recovery_skill=recovery_skill,
            outcome=outcome,
            context=dict(context or {}),
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        self._connection.execute(
            """
            INSERT INTO recovery_memory(
                memory_id, failed_skill, failure_code, recovery_skill,
                outcome, context_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.memory_id,
                record.failed_skill,
                record.failure_code,
                record.recovery_skill,
                record.outcome,
                json.dumps(record.context, ensure_ascii=False, sort_keys=True, default=str),
                record.created_at,
            ),
        )
        self._connection.commit()
        return record

    def find_successful_recoveries(
        self,
        *,
        failed_skill: str,
        failure_code: str,
        limit: int = 10,
    ) -> list[RecoveryRecord]:
        rows = self._connection.execute(
            """
            SELECT memory_id, failed_skill, failure_code, recovery_skill,
                   outcome, context_json, created_at
            FROM recovery_memory
            WHERE failed_skill = ? AND failure_code = ? AND outcome = 'success'
            ORDER BY sequence DESC LIMIT ?
            """,
            (failed_skill, failure_code, limit),
        ).fetchall()
        return [
            RecoveryRecord(
                memory_id=row["memory_id"],
                failed_skill=row["failed_skill"],
                failure_code=row["failure_code"],
                recovery_skill=row["recovery_skill"],
                outcome=row["outcome"],
                context=json.loads(row["context_json"]),
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
