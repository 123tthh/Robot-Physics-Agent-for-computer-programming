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


if __name__ == "__main__":
    unittest.main()
