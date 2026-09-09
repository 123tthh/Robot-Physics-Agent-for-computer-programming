from __future__ import annotations

import time
import unittest

from vskill_os.ipc import (
    JsonLineWorkerProcess,
    MessageGate,
    MessageHeader,
    WorkerKind,
    decode_message,
    encode_message,
    request_message,
)


def make_request(operation: str, *, sequence: int = 0, timestamp: float | None = None):
    return request_message(
        MessageHeader(
            task_id="ipc-task",
            call_id=f"call-{operation}",
            sequence=sequence,
            monotonic_time=time.monotonic() if timestamp is None else timestamp,
            source="runtime",
        ),
        worker=WorkerKind.FAKE,
        operation=operation,
        arguments={"value": 3},
    )


class IPCProtocolTests(unittest.IsolatedAsyncioTestCase):
    def test_codec_and_gate_reject_duplicate_out_of_order_and_stale(self) -> None:
        now = time.monotonic()
        message = make_request("echo", sequence=1, timestamp=now)
        restored = decode_message(encode_message(message))
        gate = MessageGate(max_age_seconds=1.0)

        self.assertEqual(restored.to_dict(), message.to_dict())
        self.assertEqual(gate.accept(message, now=now), (True, None))
        self.assertEqual(gate.accept(message, now=now)[1], "DUPLICATE_MESSAGE")
        older = request_message(
            MessageHeader(
                task_id="ipc-task",
                call_id="call-echo",
                sequence=0,
                monotonic_time=now,
                source="runtime",
            ),
            worker=WorkerKind.FAKE,
            operation="echo",
        )
        self.assertEqual(gate.accept(older, now=now)[1], "OUT_OF_ORDER_MESSAGE")
        stale = make_request("stale", timestamp=now - 2.0)
        self.assertEqual(gate.accept(stale, now=now)[1], "STALE_MESSAGE")

    async def test_local_worker_success_timeout_and_exit_are_structured(self) -> None:
        worker = JsonLineWorkerProcess()
        try:
            success = await worker.invoke(make_request("echo"), timeout_seconds=1.0)
            self.assertEqual(success[-1].payload["status"], "succeeded")

            timeout = await worker.invoke(make_request("sleep"), timeout_seconds=0.01)
            self.assertEqual(timeout[-1].payload["failure_code"], "WORKER_TIMEOUT")
        finally:
            await worker.close()

        crashed = JsonLineWorkerProcess()
        try:
            result = await crashed.invoke(make_request("crash"), timeout_seconds=1.0)
            self.assertEqual(result[-1].payload["failure_code"], "WORKER_EXITED")
        finally:
            await crashed.close()


if __name__ == "__main__":
    unittest.main()
