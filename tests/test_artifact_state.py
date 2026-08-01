import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from us_quant.artifact_state import (
    LEGACY_CROSS_SECTIONAL_REASONS,
    load_artifact_catalog,
)


class ArtifactStateTests(unittest.TestCase):
    def test_legacy_cross_sectional_result_is_not_deployable(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "market_scan.json").write_text(
                json.dumps(
                    {
                        "generated_at": "2026-07-24T00:00:00+00:00",
                        "data_date": "2026-07-23",
                    }
                ),
                encoding="utf-8",
            )
            (root / "cross_sectional_research.json").write_text(
                json.dumps(
                    {
                        "generated_at": "2026-07-24T00:00:00+00:00",
                        "scope": {"last_completed_date": "2026-07-23"},
                    }
                ),
                encoding="utf-8",
            )
            catalog = load_artifact_catalog(root)

        research = next(
            item
            for item in catalog.artifacts
            if item.artifact_type == "cross_sectional_research"
        )
        self.assertEqual(research.status, "legacy_invalidated")
        self.assertFalse(research.deployable)
        for reason in LEGACY_CROSS_SECTIONAL_REASONS:
            self.assertIn(reason, research.limitations)
        self.assertEqual(research.data_as_of, "2026-07-23")
        self.assertTrue(research.file_sha256)
        self.assertTrue(research.run_id)

    def test_load_errors_are_visible_instead_of_silenced(self) -> None:
        with TemporaryDirectory() as directory:
            catalog = load_artifact_catalog(Path(directory))
        self.assertTrue(
            all(item.status == "load_error" for item in catalog.artifacts)
        )
        self.assertTrue(
            all(item.limitations for item in catalog.artifacts)
        )
