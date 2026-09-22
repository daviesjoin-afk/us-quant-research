"""The cross-sectional research data boundary, without Qt or a window.

``DesktopCrossSectionService`` is pure application code: it reads the config it
is told to read, runs the research through the executor and writes the report.
Every test here replaces the executor and the writer, so nothing touches a real
bar file or a real research run.

The two timing rules the service owns are asserted directly: the config is read
when ``run`` is called (not when the service is constructed), and the research
capital becomes an exact ``Decimal`` initial equity.
"""

from __future__ import annotations

import ast
import json
import pathlib
from decimal import Decimal

import pytest

from us_quant.config import load_config
from us_quant.desktop_cross_section_service import DesktopCrossSectionService
from us_quant.universe import UniverseRecord, UniverseSnapshot

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
_SERVICE_PATH = (
    _REPO_ROOT / "src" / "us_quant" / "desktop_cross_section_service.py"
)
_CONFIG_PATH = _REPO_ROOT / "configs" / "paper.toml"

from datetime import datetime, timezone  # noqa: E402


def _universe(symbol: str = "AAA") -> UniverseSnapshot:
    return UniverseSnapshot(
        generated_at=datetime(2026, 9, 22, tzinfo=timezone.utc),
        source_timestamps={"test": "now"},
        records=(
            UniverseRecord(
                symbol=symbol,
                name=symbol,
                exchange="NASDAQ",
                security_type="STK",
                eligible_for_research=True,
            ),
        ),
    )


def _config():
    return load_config(_CONFIG_PATH)


def _make(
    *,
    config_provider=None,
    report_path,
    data_root=None,
    fallback_data_root=None,
):
    return DesktopCrossSectionService(
        config_provider=config_provider or _config,
        report_path=report_path,
        data_root=data_root or pathlib.Path("data"),
        fallback_data_root=fallback_data_root,
    )


# -- the constructor performs no I/O -------------------------------------


def test_the_constructor_reads_no_config(tmp_path) -> None:
    """The provider is the point, so capturing its value here would be a bug."""

    calls: list[int] = []

    def provider():
        calls.append(1)
        return _config()

    _make(config_provider=provider, report_path=tmp_path / "report.json")

    assert calls == []


def test_the_constructor_reads_no_artifact(tmp_path) -> None:
    _make(report_path=tmp_path / "report.json")

    assert list(tmp_path.iterdir()) == []


# -- run: timing, precision, plumbing ------------------------------------


def test_run_reads_the_config_when_it_is_called(tmp_path, monkeypatch) -> None:
    """A config change before ``run`` must be the one the run uses.

    Asserted by *object identity* across two runs: the executor must see the
    second config on the second run.  A constructor-time capture would hand it
    the first one twice, which is exactly the stale-startup-config bug the
    provider exists to prevent.
    """

    from dataclasses import replace

    base = _config()
    later = replace(base, base_currency="EUR")
    remaining = [base, later]

    def provider():
        return remaining.pop(0)

    service = _make(
        config_provider=provider, report_path=tmp_path / "report.json"
    )
    seen: list[object] = []
    monkeypatch.setattr(
        "us_quant.desktop_cross_section_service."
        "run_executable_cross_sectional_research",
        lambda config, universe, **kwargs: seen.append(config) or {},
    )
    monkeypatch.setattr(
        "us_quant.desktop_cross_section_service.save_executable_research",
        lambda result, path: path,
    )

    service.run(_universe(), research_capital=2500)
    service.run(_universe(), research_capital=2500)

    # The second run used the config that was current when *it* ran, not the
    # one that was current when the service was constructed.  Compared on
    # ``base_currency`` because ``run`` builds a new config with the scenario
    # capital substituted in, so the object identity does not survive.
    assert [config.base_currency for config in seen] == ["USD", "EUR"]
    assert [config.initial_equity for config in seen] == [
        Decimal(2500),
        Decimal(2500),
    ]


def test_the_config_is_not_captured_at_construction(
    tmp_path, monkeypatch
) -> None:
    """A provider whose value would be wrong if read early.

    Constructing the service must not consume the provider at all, which is
    what makes ``config_provider`` safe to wire as ``lambda: self.config``.
    """

    calls: list[int] = []

    def provider():
        calls.append(1)
        return _config()

    service = _make(
        config_provider=provider, report_path=tmp_path / "report.json"
    )
    assert calls == []

    monkeypatch.setattr(
        "us_quant.desktop_cross_section_service."
        "run_executable_cross_sectional_research",
        lambda config, universe, **kwargs: {},
    )
    monkeypatch.setattr(
        "us_quant.desktop_cross_section_service.save_executable_research",
        lambda result, path: path,
    )

    service.run(_universe(), research_capital=2500)

    assert calls == [1]


def test_research_capital_becomes_an_exact_decimal_initial_equity(
    tmp_path, monkeypatch
) -> None:
    service = _make(report_path=tmp_path / "report.json")
    seen: dict[str, object] = {}
    monkeypatch.setattr(
        "us_quant.desktop_cross_section_service."
        "run_executable_cross_sectional_research",
        lambda config, universe, **kwargs: seen.setdefault(
            "initial_equity", config.initial_equity
        )
        or {},
    )
    monkeypatch.setattr(
        "us_quant.desktop_cross_section_service.save_executable_research",
        lambda result, path: path,
    )

    service.run(_universe(), research_capital=2500)

    # No float round-trip: the executor sees exactly Decimal(2500).
    assert seen["initial_equity"] == Decimal(2500)
    assert seen["initial_equity"] == Decimal("2500")


def test_precision_survives_a_value_a_float_cannot_represent(
    tmp_path, monkeypatch
) -> None:
    number = 2**53 + 1
    service = _make(report_path=tmp_path / "report.json")
    seen: dict[str, object] = {}
    monkeypatch.setattr(
        "us_quant.desktop_cross_section_service."
        "run_executable_cross_sectional_research",
        lambda config, universe, **kwargs: seen.setdefault(
            "initial_equity", config.initial_equity
        )
        or {},
    )
    monkeypatch.setattr(
        "us_quant.desktop_cross_section_service.save_executable_research",
        lambda result, path: path,
    )

    service.run(_universe(), research_capital=number)

    assert seen["initial_equity"] == Decimal(number)
    assert Decimal(float(number)) != Decimal(number)


def test_the_same_universe_and_roots_reach_the_executor(
    tmp_path, monkeypatch
) -> None:
    data_root = tmp_path / "data"
    fallback = tmp_path / "bundled"
    service = _make(
        report_path=tmp_path / "report.json",
        data_root=data_root,
        fallback_data_root=fallback,
    )
    universe = _universe("BBB")
    seen: dict[str, object] = {}
    monkeypatch.setattr(
        "us_quant.desktop_cross_section_service."
        "run_executable_cross_sectional_research",
        lambda config, passed, **kwargs: seen.update(
            {"universe": passed, **kwargs}
        )
        or {},
    )
    monkeypatch.setattr(
        "us_quant.desktop_cross_section_service.save_executable_research",
        lambda result, path: path,
    )

    service.run(universe, research_capital=2500)

    assert seen["universe"] is universe
    assert seen["data_root"] == data_root
    assert seen["fallback_data_root"] == fallback


def test_the_result_is_saved_to_the_exact_report_path(
    tmp_path, monkeypatch
) -> None:
    report_path = tmp_path / "nested" / "report.json"
    service = _make(report_path=report_path)
    saved: list[pathlib.Path] = []
    monkeypatch.setattr(
        "us_quant.desktop_cross_section_service."
        "run_executable_cross_sectional_research",
        lambda config, universe, **kwargs: {"status": "ok"},
    )
    monkeypatch.setattr(
        "us_quant.desktop_cross_section_service.save_executable_research",
        lambda result, path: saved.append(path),
    )

    service.run(_universe(), research_capital=2500)

    assert saved == [report_path]


def test_the_result_identity_is_returned_unchanged(
    tmp_path, monkeypatch
) -> None:
    service = _make(report_path=tmp_path / "report.json")
    result = {"status": "research_exploratory"}
    monkeypatch.setattr(
        "us_quant.desktop_cross_section_service."
        "run_executable_cross_sectional_research",
        lambda config, universe, **kwargs: result,
    )
    monkeypatch.setattr(
        "us_quant.desktop_cross_section_service.save_executable_research",
        lambda value, path: path,
    )

    assert service.run(_universe(), research_capital=1) is result


def test_a_failing_run_writes_nothing(tmp_path, monkeypatch) -> None:
    service = _make(report_path=tmp_path / "report.json")
    saved: list[object] = []
    monkeypatch.setattr(
        "us_quant.desktop_cross_section_service."
        "run_executable_cross_sectional_research",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    monkeypatch.setattr(
        "us_quant.desktop_cross_section_service.save_executable_research",
        lambda result, path: saved.append(path),
    )

    with pytest.raises(RuntimeError):
        service.run(_universe(), research_capital=2500)
    assert saved == []


def test_a_failing_save_propagates(tmp_path, monkeypatch) -> None:
    """A failed write must not report success -- the old task semantics."""

    service = _make(report_path=tmp_path / "report.json")
    monkeypatch.setattr(
        "us_quant.desktop_cross_section_service."
        "run_executable_cross_sectional_research",
        lambda config, universe, **kwargs: {},
    )
    monkeypatch.setattr(
        "us_quant.desktop_cross_section_service.save_executable_research",
        lambda result, path: (_ for _ in ()).throw(OSError("disk full")),
    )

    with pytest.raises(OSError):
        service.run(_universe(), research_capital=2500)


# -- load_saved ----------------------------------------------------------


def test_a_missing_report_loads_as_none(tmp_path) -> None:
    service = _make(report_path=tmp_path / "absent.json")

    assert service.load_saved() is None


def test_a_valid_report_loads_as_a_dict(tmp_path) -> None:
    path = tmp_path / "report.json"
    report = {"out_of_sample": {"strategy": {"total_return": 0.2}}}
    path.write_text(json.dumps(report), encoding="utf-8")
    service = _make(report_path=path)

    assert service.load_saved() == report


def test_malformed_json_raises(tmp_path) -> None:
    path = tmp_path / "report.json"
    path.write_text("{bad-json", encoding="utf-8")
    service = _make(report_path=path)

    with pytest.raises(json.JSONDecodeError):
        service.load_saved()


def test_a_non_object_top_level_raises(tmp_path) -> None:
    path = tmp_path / "report.json"
    path.write_text("[1, 2, 3]", encoding="utf-8")
    service = _make(report_path=path)

    with pytest.raises(TypeError):
        service.load_saved()


# -- the module's own boundary -------------------------------------------


def test_the_service_imports_no_qt() -> None:
    """The Qt half -- the dialog, the progress copy, the thread -- stays out."""

    tree = ast.parse(_SERVICE_PATH.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)

    assert not any(
        module == "PySide6" or module.startswith("PySide6.")
        for module in modules
    )
    for forbidden in (
        "us_quant.desktop",
        "us_quant.desktop_workers",
        "us_quant.desktop_tasks",
        "us_quant.desktop_v2",
    ):
        assert forbidden not in modules
