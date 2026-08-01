from pathlib import Path
from tempfile import TemporaryDirectory
from concurrent.futures import ThreadPoolExecutor
import unittest

from us_quant.export_service import export_terminal_bundle
from us_quant.runtime_events import RuntimeEventStore
from us_quant.strategy_registry import StrategyRegistry


class RuntimeExportTests(unittest.TestCase):
    def test_runtime_event_writes_wait_instead_of_locking(self) -> None:
        with TemporaryDirectory() as directory:
            store = RuntimeEventStore(
                Path(directory) / "events.sqlite3"
            )

            def add(index: int) -> None:
                store.add(
                    severity="info",
                    component="test",
                    code=str(index),
                    message="concurrent",
                )

            with ThreadPoolExecutor(max_workers=8) as pool:
                list(pool.map(add, range(80)))
            self.assertEqual(len(store.list_recent(100)), 80)

    def test_runtime_events_and_export_bundle(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            events = RuntimeEventStore(root / "events.sqlite3")
            event = events.add(
                severity="warning",
                component="market_data",
                code="10197",
                message=(
                    "competing session token=SUPERSECRET "
                    "api_key=ALSOSECRET"
                ),
            )
            registry = StrategyRegistry(root / "strategies.sqlite3")
            registry.seed_defaults()
            output = export_terminal_bundle(
                root / "exports",
                portfolio=None,
                stream=None,
                strategies=registry.list_records(),
                events=events.list_recent(),
            )
            manifest = (output / "manifest.json").read_text(
                encoding="utf-8"
            )
            strategies_csv = (
                output / "strategies.csv"
            ).read_text(encoding="utf-8-sig")
            replay_csv = (
                output / "targeted_replays.csv"
            ).read_text(encoding="utf-8-sig")
            robustness_csv = (
                output / "targeted_robustness.csv"
            ).read_text(encoding="utf-8-sig")
            walk_forward_csv = (
                output / "targeted_walk_forward.csv"
            ).read_text(encoding="utf-8-sig")
            overfit_csv = (
                output / "targeted_overfit.csv"
            ).read_text(encoding="utf-8-sig")
            quality_csv = (
                output / "targeted_data_quality.csv"
            ).read_text(encoding="utf-8-sig")
            stress_csv = (
                output / "targeted_execution_stress.csv"
            ).read_text(encoding="utf-8-sig")
            review_csv = (
                output / "targeted_review.csv"
            ).read_text(encoding="utf-8-sig")
            paper_orders_csv = (
                output / "paper_orders.csv"
            ).read_text(encoding="utf-8-sig")
            paper_executions_csv = (
                output / "paper_executions.csv"
            ).read_text(encoding="utf-8-sig")
            all_text = "\n".join(
                path.read_text(
                    encoding=(
                        "utf-8-sig"
                        if path.suffix == ".csv"
                        else "utf-8"
                    )
                )
                for path in output.iterdir()
            )
        self.assertEqual(event.code, "10197")
        self.assertIn("masked", manifest)
        self.assertIn("sector-momentum", strategies_csv)
        self.assertEqual(replay_csv, "")
        self.assertEqual(robustness_csv, "")
        self.assertEqual(walk_forward_csv, "")
        self.assertEqual(overfit_csv, "")
        self.assertEqual(quality_csv, "")
        self.assertEqual(stress_csv, "")
        self.assertEqual(review_csv, "")
        self.assertEqual(paper_orders_csv, "")
        self.assertEqual(paper_executions_csv, "")
        self.assertIn("targeted_replays.csv", manifest)
        self.assertIn("targeted_robustness.csv", manifest)
        self.assertIn("targeted_walk_forward.csv", manifest)
        self.assertIn("targeted_overfit.csv", manifest)
        self.assertIn("targeted_data_quality.csv", manifest)
        self.assertIn("targeted_execution_stress.csv", manifest)
        self.assertIn("targeted_review.csv", manifest)
        self.assertIn("paper_orders.csv", manifest)
        self.assertIn("paper_executions.csv", manifest)
        self.assertNotIn("SUPERSECRET", all_text)
        self.assertNotIn("ALSOSECRET", all_text)
        self.assertIn("[REDACTED]", all_text)
