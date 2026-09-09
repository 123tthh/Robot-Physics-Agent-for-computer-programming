"""Versioned JSON-lines protocol for optional simulation and desktop workers."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from enum import StrEnum
import json
import math
import sys
import time
from typing import Any, Callable, Mapping, Protocol, Sequence
from uuid import uuid4


PROTOCOL_VERSION = "1.0"


class WorkerKind(StrEnum):
    ISAAC = "isaac"
    DESKTOP = "desktop"
    MODEL = "model"
    FAKE = "fake"


class MessageKind(StrEnum):
    REQUEST = "request"
    PROGRESS = "progress"
    CANCEL = "cancel"
    RESULT = "result"
    HEALTH = "health"
    KEYCAP_STATE = "keycap_state"
    KEY_EVENT = "key_event"
    SCREEN_FRAME = "screen_frame"


class WorkerResultStatus(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELED = "canceled"


class WorkerHealthStatus(StrEnum):
    STARTING = "starting"
    READY = "ready"
    DEGRADED = "degraded"
    STOPPED = "stopped"


@dataclass(frozen=True, slots=True)
class MessageHeader:
    task_id: str
    call_id: str
    sequence: int
    monotonic_time: float
    source: str
    message_id: str = field(default_factory=lambda: uuid4().hex)
    protocol_version: str = PROTOCOL_VERSION

    def __post_init__(self) -> None:
        if not self.task_id or not self.call_id or not self.source:
            raise ValueError("task_id, call_id, and source are required")
        if self.sequence < 0:
            raise ValueError("message sequence cannot be negative")
        if not math.isfinite(self.monotonic_time):
            raise ValueError("message monotonic_time must be finite")

    def to_dict(self) -> dict[str, Any]:
        return {
            "protocol_version": self.protocol_version,
            "task_id": self.task_id,
            "call_id": self.call_id,
            "message_id": self.message_id,
            "sequence": self.sequence,
            "monotonic_time": self.monotonic_time,
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "MessageHeader":
        return cls(
            task_id=str(data["task_id"]),
            call_id=str(data["call_id"]),
            message_id=str(data["message_id"]),
            sequence=int(data["sequence"]),
            monotonic_time=float(data["monotonic_time"]),
            source=str(data["source"]),
            protocol_version=str(data.get("protocol_version", PROTOCOL_VERSION)),
        )


@dataclass(frozen=True, slots=True)
class WorkerMessage:
    kind: MessageKind
    header: MessageHeader
    payload: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "header": self.header.to_dict(),
            "payload": dict(self.payload),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "WorkerMessage":
        payload = data.get("payload", {})
        if not isinstance(payload, dict):
            raise ValueError("worker message payload must be an object")
        return cls(
            kind=MessageKind(str(data["kind"])),
            header=MessageHeader.from_dict(data["header"]),
            payload=payload,
        )


def make_header(
    *,
    task_id: str,
    call_id: str,
    sequence: int,
    source: str,
    clock: Callable[[], float] = time.monotonic,
) -> MessageHeader:
    return MessageHeader(task_id, call_id, sequence, clock(), source)


def request_message(
    header: MessageHeader,
    *,
    worker: WorkerKind,
    operation: str,
    arguments: Mapping[str, Any] | None = None,
) -> WorkerMessage:
    if not operation:
        raise ValueError("worker operation is required")
    return WorkerMessage(
        MessageKind.REQUEST,
        header,
        {"worker": worker.value, "operation": operation, "arguments": dict(arguments or {})},
    )


def cancel_message(header: MessageHeader, *, reason: str) -> WorkerMessage:
    return WorkerMessage(MessageKind.CANCEL, header, {"reason": reason})


def progress_message(
    header: MessageHeader,
    *,
    phase: str,
    progress: float,
    details: Mapping[str, Any] | None = None,
) -> WorkerMessage:
    if not 0.0 <= progress <= 1.0:
        raise ValueError("worker progress must be within [0, 1]")
    return WorkerMessage(
        MessageKind.PROGRESS,
        header,
        {"phase": phase, "progress": progress, "details": dict(details or {})},
    )


def result_message(
    header: MessageHeader,
    *,
    status: WorkerResultStatus,
    output: Mapping[str, Any] | None = None,
    failure_code: str | None = None,
    message: str = "",
) -> WorkerMessage:
    return WorkerMessage(
        MessageKind.RESULT,
        header,
        {
            "status": status.value,
            "output": dict(output or {}),
            "failure_code": failure_code,
            "message": message,
        },
    )


def health_message(
    header: MessageHeader,
    *,
    worker: WorkerKind,
    status: WorkerHealthStatus,
    details: Mapping[str, Any] | None = None,
) -> WorkerMessage:
    return WorkerMessage(
        MessageKind.HEALTH,
        header,
        {"worker": worker.value, "status": status.value, "details": dict(details or {})},
    )


def encode_message(message: WorkerMessage) -> bytes:
    return (json.dumps(message.to_dict(), ensure_ascii=False, sort_keys=True) + "\n").encode(
        "utf-8"
    )


def decode_message(data: bytes | str) -> WorkerMessage:
    text = data.decode("utf-8") if isinstance(data, bytes) else data
    decoded = json.loads(text)
    if not isinstance(decoded, dict):
        raise ValueError("worker message must decode to an object")
    return WorkerMessage.from_dict(decoded)


class MessageGate:
    """Reject duplicate, out-of-order, wrong-version, and stale worker messages."""

    def __init__(self, *, max_age_seconds: float = 5.0) -> None:
        if max_age_seconds <= 0.0:
            raise ValueError("max_age_seconds must be positive")
        self.max_age_seconds = max_age_seconds
        self._seen_ids: set[str] = set()
        self._last_sequence: dict[tuple[str, str], int] = {}

    def accept(self, message: WorkerMessage, *, now: float) -> tuple[bool, str | None]:
        header = message.header
        if header.protocol_version != PROTOCOL_VERSION:
            return False, "PROTOCOL_VERSION_MISMATCH"
        if header.message_id in self._seen_ids:
            return False, "DUPLICATE_MESSAGE"
        key = (header.source, header.call_id)
        if header.sequence <= self._last_sequence.get(key, -1):
            return False, "OUT_OF_ORDER_MESSAGE"
        if now - header.monotonic_time > self.max_age_seconds:
            return False, "STALE_MESSAGE"
        self._seen_ids.add(header.message_id)
        self._last_sequence[key] = header.sequence
        return True, None


class WorkerProcessError(RuntimeError):
    pass


class WorkerTransport(Protocol):
    async def invoke(
        self,
        request: WorkerMessage,
        *,
        timeout_seconds: float,
    ) -> tuple[WorkerMessage, ...]:
        """Run one bounded worker request and return protocol messages."""

    async def close(self) -> None:
        """Cancel outstanding work and release the transport."""


class JsonLineWorkerProcess:
    """Small subprocess transport used to prove timeout/cancel/exit semantics."""

    def __init__(self, command: Sequence[str] | None = None) -> None:
        self.command = tuple(command or (sys.executable, "-m", "vskill_os.worker_stub"))
        self.process: asyncio.subprocess.Process | None = None

    async def start(self) -> None:
        if self.process is not None and self.process.returncode is None:
            return
        self.process = await asyncio.create_subprocess_exec(
            *self.command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

    async def invoke(
        self,
        request: WorkerMessage,
        *,
        timeout_seconds: float,
    ) -> tuple[WorkerMessage, ...]:
        if request.kind is not MessageKind.REQUEST:
            raise ValueError("invoke requires a request message")
        await self.start()
        assert self.process is not None and self.process.stdin is not None
        assert self.process.stdout is not None
        self.process.stdin.write(encode_message(request))
        await self.process.stdin.drain()
        messages: list[WorkerMessage] = []

        async def read_until_result() -> None:
            while True:
                line = await self.process.stdout.readline()
                if not line:
                    raise WorkerProcessError(
                        f"worker exited before result (returncode={self.process.returncode})"
                    )
                message = decode_message(line)
                if message.header.call_id != request.header.call_id:
                    continue
                messages.append(message)
                if message.kind is MessageKind.RESULT:
                    return

        try:
            await asyncio.wait_for(read_until_result(), timeout_seconds)
        except TimeoutError:
            cancel = cancel_message(
                MessageHeader(
                    request.header.task_id,
                    request.header.call_id,
                    request.header.sequence + 1,
                    time.monotonic(),
                    "vskill-runtime",
                ),
                reason="request timeout",
            )
            self.process.stdin.write(encode_message(cancel))
            await self.process.stdin.drain()
            await self._terminate_process()
            messages.append(
                result_message(
                    MessageHeader(
                        request.header.task_id,
                        request.header.call_id,
                        request.header.sequence + 2,
                        time.monotonic(),
                        "vskill-runtime",
                    ),
                    status=WorkerResultStatus.CANCELED,
                    failure_code="WORKER_TIMEOUT",
                    message="worker request timed out and cancellation was sent",
                )
            )
        except WorkerProcessError as exc:
            messages.append(
                result_message(
                    MessageHeader(
                        request.header.task_id,
                        request.header.call_id,
                        request.header.sequence + 1,
                        time.monotonic(),
                        "vskill-runtime",
                    ),
                    status=WorkerResultStatus.FAILED,
                    failure_code="WORKER_EXITED",
                    message=str(exc),
                )
            )
        return tuple(messages)

    async def close(self) -> None:
        await self._terminate_process()

    async def _terminate_process(self) -> None:
        process = self.process
        self.process = None
        if process is None or process.returncode is not None:
            return
        if process.stdin is not None:
            process.stdin.close()
        process.terminate()
        try:
            await asyncio.wait_for(process.wait(), 1.0)
        except TimeoutError:
            process.kill()
            await process.wait()

    async def __aenter__(self) -> "JsonLineWorkerProcess":
        await self.start()
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()
