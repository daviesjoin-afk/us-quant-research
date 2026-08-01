from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from us_quant.paths import ApplicationPaths


class ApplicationPathsTests(unittest.TestCase):
    def test_resource_and_state_roots_are_separate(self) -> None:
        with TemporaryDirectory() as resources, TemporaryDirectory() as state:
            paths = ApplicationPaths.discover(
                resource_root=Path(resources),
                state_root=Path(state),
            )
            paths.ensure_state_directories()

            self.assertNotEqual(paths.resource_root, paths.state_root)
            self.assertTrue(paths.runtime_root.is_dir())
            self.assertTrue(paths.logs_root.is_dir())
            self.assertTrue(paths.exports_root.is_dir())
            self.assertTrue(paths.user_data_root.is_dir())

    def test_baseline_results_seed_only_missing_user_files(self) -> None:
        with TemporaryDirectory() as resources, TemporaryDirectory() as state:
            resource_root = Path(resources)
            bundled = resource_root / "research" / "results"
            bundled.mkdir(parents=True)
            (bundled / "baseline.json").write_text(
                '{"source":"bundle"}', encoding="utf-8"
            )
            paths = ApplicationPaths.discover(
                resource_root=resource_root,
                state_root=Path(state),
            )
            paths.ensure_state_directories()
            paths.seed_research_results()
            target = paths.research_results_root / "baseline.json"
            self.assertEqual(
                target.read_text(encoding="utf-8"),
                '{"source":"bundle"}',
            )
            target.write_text('{"source":"user"}', encoding="utf-8")
            paths.seed_research_results()
            self.assertEqual(
                target.read_text(encoding="utf-8"),
                '{"source":"user"}',
            )
