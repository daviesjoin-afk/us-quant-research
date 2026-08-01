from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from us_quant.strategy_registry import (
    StrategyRegistry,
    StrategyRegistryError,
)


class StrategyRegistryTests(unittest.TestCase):
    def test_caller_cannot_self_attest_gate_pass(self) -> None:
        with TemporaryDirectory() as directory:
            registry = StrategyRegistry(
                Path(directory) / "strategies.sqlite3"
            )
            with self.assertRaisesRegex(
                StrategyRegistryError, "不能自行声明"
            ):
                registry.register(
                    strategy_id="unsafe",
                    name="unsafe",
                    description="test",
                    semver="1.0.0",
                    parameters={},
                    universe_hash="u",
                    code_hash="c",
                    risk_budget_pct=0.10,
                    gate_passed=True,
                )

    def test_versions_are_immutable_and_clone_creates_new_version(self) -> None:
        with TemporaryDirectory() as directory:
            registry = StrategyRegistry(
                Path(directory) / "strategies.sqlite3"
            )
            first = registry.register(
                strategy_id="test",
                name="Test",
                description="test",
                semver="1.0.0",
                parameters={"lookback": 20},
                universe_hash="u",
                code_hash="c",
                risk_budget_pct=0.10,
            )
            second = registry.clone_version(
                first.version_id,
                semver="1.0.1",
                parameters={"lookback": 21},
            )
            original = registry.get_version(first.version_id)
        self.assertNotEqual(first.version_id, second.version_id)
        self.assertEqual(original.parameters, {"lookback": 20})
        self.assertEqual(second.parameters, {"lookback": 21})

    def test_known_family_rejects_invalid_parameter_clone(self) -> None:
        with TemporaryDirectory() as directory:
            registry = StrategyRegistry(
                Path(directory) / "strategies.sqlite3"
            )
            registry.seed_defaults()
            source = next(
                record
                for record in registry.list_records()
                if record.strategy_id == "dual-ma-trend"
            )
            with self.assertRaises(ValueError):
                registry.clone_version(
                    source.version_id,
                    semver="invalid",
                    parameters={
                        "short_window": 200,
                        "long_window": 20,
                        "whole_shares": True,
                    },
                )

    def test_unapproved_research_cannot_enter_shadow(self) -> None:
        with TemporaryDirectory() as directory:
            registry = StrategyRegistry(
                Path(directory) / "strategies.sqlite3"
            )
            record = registry.register(
                strategy_id="blocked",
                name="Blocked",
                description="test",
                semver="1",
                parameters={},
                universe_hash="u",
                code_hash="c",
                risk_budget_pct=0.10,
                gate_reason="cost stress failed",
            )
            with self.assertRaises(StrategyRegistryError):
                registry.transition(
                    record.version_id,
                    "paper_shadow",
                    reason="try",
                )

    def test_legacy_invalidated_has_no_transitions(self) -> None:
        with TemporaryDirectory() as directory:
            registry = StrategyRegistry(
                Path(directory) / "strategies.sqlite3"
            )
            record = registry.register(
                strategy_id="legacy",
                name="Legacy",
                description="test",
                semver="1",
                parameters={},
                universe_hash="u",
                code_hash="c",
                risk_budget_pct=0.10,
                status="legacy_invalidated",
            )
            with self.assertRaises(StrategyRegistryError):
                registry.transition(
                    record.version_id,
                    "paper_shadow",
                    reason="try",
                )
            with self.assertRaisesRegex(
                StrategyRegistryError, "不能克隆"
            ):
                registry.clone_version(
                    record.version_id,
                    semver="2",
                    parameters={},
                )

    def test_execution_mapping_is_not_seeded_as_alpha_strategy(self) -> None:
        with TemporaryDirectory() as directory:
            registry = StrategyRegistry(
                Path(directory) / "strategies.sqlite3"
            )
            registry.seed_defaults()
            records = registry.list_records()
        self.assertFalse(
            any(
                "signal_symbol" in row.parameters
                or "execution_symbol" in row.parameters
                for row in records
            )
        )

    def test_seed_migrates_embedded_symbols_to_read_only_legacy(self) -> None:
        with TemporaryDirectory() as directory:
            registry = StrategyRegistry(
                Path(directory) / "strategies.sqlite3"
            )
            legacy = registry.register(
                strategy_id="embedded-symbol-example",
                name="Embedded symbols",
                description="old run-specific parameter model",
                semver="0.1.0",
                parameters={
                    "signal_symbol": "ABC",
                    "execution_symbol": "ALT",
                    "whole_shares": True,
                },
                universe_hash="legacy-pair",
                code_hash="legacy",
                risk_budget_pct=0.10,
            )
            registry.seed_defaults()
            migrated = registry.get_version(legacy.version_id)
        self.assertEqual(migrated.status, "legacy_invalidated")
        self.assertEqual(migrated.mode, "research")

    def test_defaults_add_new_families_to_existing_registry(self) -> None:
        with TemporaryDirectory() as directory:
            registry = StrategyRegistry(
                Path(directory) / "strategies.sqlite3"
            )
            registry.register(
                strategy_id="existing",
                name="Existing",
                description="test",
                semver="1",
                parameters={},
                universe_hash="u",
                code_hash="c",
                risk_budget_pct=0.10,
            )
            registry.seed_defaults()
            strategy_ids = {
                record.strategy_id
                for record in registry.list_records()
            }
        self.assertTrue(
            {
                "buy-hold",
                "dual-ma-trend",
                "donchian-breakout",
                "rsi-mean-reversion",
                "intraday-targeted-t",
                "intraday-auto-rotation",
            }.issubset(strategy_ids)
        )
