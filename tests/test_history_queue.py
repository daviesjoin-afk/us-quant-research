from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from us_quant.history_queue import HistoryJobStore


class HistoryQueueTests(unittest.TestCase):
    def test_schedule_claim_complete_and_resume(self) -> None:
        with TemporaryDirectory() as directory:
            store = HistoryJobStore(Path(directory) / "jobs.sqlite3")
            self.assertEqual(
                store.schedule(("NVDA", "AAPL", "NVDA")),
                2,
            )
            claimed = store.claim(1)
            self.assertEqual(len(claimed), 1)
            self.assertEqual(claimed[0].symbol, "NVDA")
            store.complete("NVDA", 1250)
            counts = store.counts()
            self.assertEqual(counts["completed"], 1)
            self.assertEqual(counts["pending"], 1)

    def test_stale_running_is_recoverable(self) -> None:
        with TemporaryDirectory() as directory:
            store = HistoryJobStore(Path(directory) / "jobs.sqlite3")
            store.schedule(("MSFT",))
            store.claim(1)
            self.assertEqual(store.reset_stale_running(), 1)
            self.assertEqual(store.counts()["pending"], 1)


if __name__ == "__main__":
    unittest.main()
