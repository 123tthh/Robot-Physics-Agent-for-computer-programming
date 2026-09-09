"""Deterministic local JSON-lines worker used by protocol acceptance tests."""

from __future__ import annotations

import json
import os
import sys
import time

from .ipc import (
    MessageHeader,
    MessageKind,
    WorkerMessage,
    WorkerResultStatus,
    encode_message,
    result_message,
)


def _send(message: WorkerMessage) -> None:
    sys.stdout.buffer.write(encode_message(message))
    sys.stdout.buffer.flush()


def main() -> int:
    for line in sys.stdin.buffer:
        message = WorkerMessage.from_dict(json.loads(line))
        if message.kind is MessageKind.CANCEL:
            continue
        if message.kind is not MessageKind.REQUEST:
            continue
        operation = str(message.payload.get("operation", ""))
        arguments = dict(message.payload.get("arguments", {}))
        if operation == "crash":
            os._exit(17)
        if operation == "sleep":
            time.sleep(float(arguments.get("seconds", 1.0)))
        header = MessageHeader(
            task_id=message.header.task_id,
            call_id=message.header.call_id,
            sequence=message.header.sequence + 1,
            monotonic_time=time.monotonic(),
            source="fake-worker",
        )
        _send(
            result_message(
                header,
                status=WorkerResultStatus.SUCCEEDED,
                output={"echo": arguments},
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
