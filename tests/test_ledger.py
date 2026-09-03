from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from vskill_os.ledger import EventLedger


class LedgerTests(unittest.TestCase):
    def test_events_are_persisted_in_order(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "events.db"
            with EventLedger(path) as ledger:
                ledger.append(task_id="t1", kind="first", payload={"index": 1})
                ledger.append(task_id="t1", kind="second", payload={"index": 2})

            with EventLedger(path) as ledger:
                events = ledger.list_events("t1")

            self.assertEqual([event.kind for event in events], ["first", "second"])
            self.assertEqual(events[1].payload, {"index": 2})

    def test_checkpoint_and_recovery_memory_round_trip(self) -> None:
        with EventLedger() as ledger:
            ledger.save_checkpoint(
                task_id="t2",
                state={"facts": ["a"], "values": {}, "revision": 1},
                completed_nodes={"n1"},
            )
            ledger.remember_recovery(
                failed_skill="press_chord",
                failure_code="ALT_MISSED",
                recovery_skill="recalibrate_keyboard",
                outcome="success",
                context={"keyboard_shift_mm": 18},
            )
            checkpoint = ledger.load_latest_checkpoint("t2")
            memories = ledger.find_successful_recoveries(
                failed_skill="press_chord",
                failure_code="ALT_MISSED",
            )

        self.assertIsNotNone(checkpoint)
        assert checkpoint is not None
        self.assertEqual(checkpoint.completed_nodes, ("n1",))
        self.assertEqual(memories[0].recovery_skill, "recalibrate_keyboard")


if __name__ == "__main__":
    unittest.main()
