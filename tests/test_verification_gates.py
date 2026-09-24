"""Guards for the verification gates themselves.

``scripts/verify.ps1`` and ``.github/workflows/ci.yml`` are the things that decide
whether anything else is allowed to merge, so a quiet weakening of either is the
most expensive possible regression in this repository -- worse than any single
production bug, because it removes the mechanism that would have caught it.

These guards exist because that failure mode is not hypothetical.  Every one of
the mutations below was applied to the script by hand and *passed* before this
file was written:

* make the pytest invocation conditional, so a bare ``verify.ps1`` stops running
  the suite;
* let ``-CoreOnly`` reassign the pytest arguments, so the "core" tier quietly
  becomes a subset;
* compute the focused flag and then ignore it, so ``-TestPath`` runs everything;
* drop the desktop self-test from the full gate;
* drop ``doctor``.

The assertions are structural rather than byte-exact: a legitimate reformat must
not fail here, but a *semantic* edit must.  So each check names the property it
protects (which invocation, under which condition) instead of pinning a line.

What this file deliberately does not do: run ``verify.ps1`` end to end.  That is
what CI does, on the primary Python version, and a nested full suite inside the
suite would double the runtime this round exists to reduce.
"""

from __future__ import annotations

import pathlib
import re

import pytest

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
_VERIFY = _REPO_ROOT / "scripts" / "verify.ps1"
_CI = _REPO_ROOT / ".github" / "workflows" / "ci.yml"
_PYPROJECT = _REPO_ROOT / "pyproject.toml"


def _verify_lines() -> list[str]:
    return _VERIFY.read_text(encoding="utf-8").splitlines()


def _ci_text() -> str:
    return _CI.read_text(encoding="utf-8")


def _invocations() -> list[tuple[int, str]]:
    """Every ``Invoke-Checked`` call as ``(line number, line)``, 1-based.

    The step name in the preceding ``Write-Step`` is what tells a reader which
    gate a call belongs to, so the line number is carried for the paired lookups
    below.
    """

    return [
        (index, line)
        for index, line in enumerate(_verify_lines(), 1)
        if "Invoke-Checked" in line
    ]


def _verify_code() -> list[str]:
    """Only the script's executable lines: comments and the header removed.

    The header documents the tiers, and the guards below assert things *about*
    that documentation -- for example that ``-n auto`` is deliberately not used.
    Reading those as code would make a guard fire on its own explanation, so the
    ``#``-prefixed lines are dropped first.
    """

    return [
        line
        for line in _verify_lines()
        if not line.lstrip().startswith("#")
    ]


def _verify_text() -> str:
    return "\n".join(_verify_code())


def _param_block() -> str:
    """The ``param(...)`` declaration, comments excluded.

    Scans for the matching close paren rather than splitting on the first one:
    ``[string[]]$TestPath = @()`` contains a paren of its own, so a naive split
    truncates the block before ``$Workers`` is declared.
    """

    code = "\n".join(_verify_code())
    start = code.index("param(")
    depth = 0
    for offset, char in enumerate(code[start:], start):
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return code[start : offset + 1]
    raise AssertionError("unterminated param block")


def _line_of(needle: str) -> int:
    for index, line in enumerate(_verify_lines(), 1):
        if needle in line:
            return index
    raise AssertionError(f"{needle!r} not found in verify.ps1")


def _indent_of(line: str) -> int:
    return len(line) - len(line.lstrip())


# -- the three tiers exist and are named --------------------------------


def test_the_script_takes_the_documented_parameters() -> None:
    """Two switches and two selectors, spelled as the task instructions assume."""

    param_block = _param_block()
    assert "[switch]$CoreOnly" in param_block
    assert "[string[]]$TestPath" in param_block
    assert "[int]$Workers" in param_block


def test_no_other_parameter_can_narrow_a_gate() -> None:
    """A new switch that disables a gate must be an explicit, reviewed decision."""

    param_block = _param_block()
    for forbidden in (
        "-SkipTests",
        "-SkipPytest",
        "-FastOnly",
        "-NoTests",
    ):
        assert forbidden not in param_block, forbidden


def test_positional_binding_is_disabled() -> None:
    """Found while validating this round's own script: ``-File`` cannot carry an array.

    ``pwsh -File scripts\\verify.ps1 -TestPath a b`` passes ``a`` and ``b`` as
    literal strings.  ``a`` binds to ``-TestPath``, and ``b`` then binds
    positionally to the *next* declared parameter -- ``-Workers`` -- producing a
    confusing "cannot convert to Int32".  Before ``-Workers`` existed the stray
    value would simply have been ignored.

    Neither outcome is acceptable for a verification entry point, so positional
    binding is off and the misuse fails loudly.  The documented calling
    convention is a direct invocation with a real array, which this test also
    pins.
    """

    source = _VERIFY.read_text(encoding="utf-8")
    assert "[CmdletBinding(PositionalBinding = $false)]" in source
    # And the header tells the reader which convention to use.
    assert "Do **not** use ``pwsh -File" in source
    assert '.\\scripts\\verify.ps1 -TestPath @("tests/a.py", "tests/b.py")' in source


# -- the bare invocation is unchanged ------------------------------------


def test_the_bare_invocation_still_runs_every_test() -> None:
    """The one invocation every PR uses, and the one that must never narrow.

    Three properties together, because the obvious mutation dodges any one of
    them: the pytest call exists exactly once, it sits at the top level of the
    ``try`` body (four spaces, not nested), and its statement *begins* with
    ``Invoke-Checked`` -- so wrapping it in a one-line ``if (...) { ... }`` at the
    same indentation, which is how the suite quietly became conditional, still
    fails here.
    """

    lines = _verify_lines()
    pytest_calls = [
        (index, line)
        for index, line in enumerate(lines, 1)
        if "Invoke-Checked" in line and "$pytestArgs" in line
    ]
    assert len(pytest_calls) == 1, pytest_calls
    index, line = pytest_calls[0]

    stripped = line.strip()
    assert _indent_of(line) == 4, (
        f"the pytest call at line {index} is nested; a bare run must always run "
        f"the full suite"
    )
    assert stripped.startswith("Invoke-Checked"), (
        f"the pytest call at line {index} is wrapped in a condition: {stripped!r}"
    )
    assert "if" not in stripped, stripped


def test_the_focused_flag_cannot_be_overwritten() -> None:
    """``$focused`` means "``-TestPath`` was passed" and nothing else.

    The mutation this guards is subtle and passes every other check: compute the
    flag correctly, then reassign it.  So the assertion is on the *definition*
    being unique and derived from the count.
    """

    assignments = [
        line.strip()
        for line in _verify_lines()
        if re.match(r"^\s*\$focused\s*=", line)
    ]
    assert assignments == ["$focused = $TestPath.Count -gt 0"], assignments


def test_core_only_never_touches_the_test_selection() -> None:
    """``-CoreOnly`` is "do not start Qt", not "run fewer tests".

    Asserted as a property: no line that assigns the pytest arguments may also
    mention ``$CoreOnly``, so the core tier cannot become a subset by accident.
    """

    offenders = [
        line.strip()
        for line in _verify_lines()
        if "$pytestArgs" in line
        and re.search(r"\$pytestArgs\s*[+]?=", line)
        and "$CoreOnly" in line
    ]
    assert not offenders, offenders


def test_the_test_path_is_appended_under_the_focused_condition() -> None:
    """``-TestPath`` narrows by *appending* paths inside the focused branch.

    Appending is what makes the focused run a strict subset of the full one: the
    base arguments (``-m pytest -q``) are unchanged, so nothing about the full
    gate's configuration is lost.
    """

    lines = _verify_lines()
    append_lines = [
        (index, line)
        for index, line in enumerate(lines, 1)
        if "$pytestArgs +=" in line and "$TestPath" in line
    ]
    assert len(append_lines) == 1, append_lines
    index, line = append_lines[0]

    # It must sit inside an ``if ($focused)`` block: the nearest preceding
    # condition at a lower indent is that guard.
    enclosing = None
    for candidate in reversed(lines[: index - 1]):
        if candidate.strip().startswith("if ") or candidate.strip().startswith("}"):
            enclosing = candidate.strip()
            break
    assert enclosing is not None and enclosing.startswith("if ($focused)"), (
        f"the -TestPath append at line {index} is not guarded by $focused "
        f"(found {enclosing!r})"
    )


# -- every tier keeps the cheap, high-value gates ------------------------


def test_doctor_runs_unconditionally() -> None:
    """It costs ~0.3 s and catches repository/config breakage in every tier."""

    calls = [
        (index, line)
        for index, line in enumerate(_verify_lines(), 1)
        if "Invoke-Checked" in line and '"doctor"' in line
    ]
    assert len(calls) == 1, calls
    index, line = calls[0]
    assert _indent_of(line) == 4, (
        f"doctor at line {index} is nested; it must run in every tier"
    )


def test_compileall_runs_unconditionally() -> None:
    """Same reasoning as doctor: cheap, and it catches syntax/import breakage."""

    calls = [
        (index, line)
        for index, line in enumerate(_verify_lines(), 1)
        if "Invoke-Checked" in line and "compileall" in line
    ]
    assert len(calls) == 1, calls
    index, line = calls[0]
    assert _indent_of(line) == 4, (
        f"compileall at line {index} is nested; it must run in every tier"
    )


def test_the_desktop_self_test_belongs_to_the_full_gate_alone() -> None:
    """Two conditions, and both are load-bearing.

    ``-CoreOnly`` excludes it because a pure-service checkpoint should not pay
    for Qt startup; ``$focused`` excludes it because a handful of files should
    not either.  Dropping either condition widens the tier, and dropping the
    whole block would remove the only gate that starts a real application.
    """

    source = _verify_text()
    assert "if (-not $CoreOnly -and -not $focused) {" in source
    assert 'US_QUANT_SELF_TEST = "1"' in source
    assert '"offscreen"' in source
    assert 'CommandArguments @("desktop_main.py")' in source


def test_the_self_test_still_preflights_the_import() -> None:
    """``import PySide6`` first, so a missing toolkit fails with its own message."""

    assert '"import PySide6"' in _verify_text()


# -- workers -------------------------------------------------------------


def test_workers_zero_means_sequential_not_auto() -> None:
    """``0`` must never be reinterpreted as ``-n auto``.

    ``auto`` on this suite is how a runner exhausts CPU and RAM: it builds Qt
    applications, ``MainWindow`` instances, SQLite files and temp trees, and an
    unbounded worker count multiplies all four.
    """

    lines = _verify_lines()
    guarded = [
        (index, line)
        for index, line in enumerate(lines, 1)
        if "$Workers -gt 0" in line
    ]
    assert len(guarded) == 1, guarded

    code = _verify_text()
    assert '"auto"' not in code
    assert "'auto'" not in code
    # And the default really is zero.
    assert "[int]$Workers = 0" in _param_block()


def test_parallel_uses_loadscope() -> None:
    """``loadscope`` keeps a module's tests on one worker.

    The default ``load`` distributor scatters a module's tests across workers,
    which breaks module-level fixtures, Qt global state and per-module temp
    directories.
    """

    lines = [
        line for line in _verify_lines() if "$pytestArgs" in line
    ]
    joined = "\n".join(lines)
    assert "loadscope" in joined
    assert "--dist=load" not in joined.replace("--dist=loadscope", "")


# -- CI -----------------------------------------------------------------


def test_ci_gates_the_primary_python_version_and_nothing_else() -> None:
    """Exactly one interpreter gates a pull request, and it is the primary one.

    This guard is the **inverse** of the one it replaces, deliberately.  It used to
    require ``["3.12", "3.13"]``, because the point then was "every supported version runs
    the complete suite".  The point now is "one interpreter is the gate, and it is the one
    the project is developed on" -- so the same assertion, still stated literally, had to
    be inverted rather than dropped: re-adding a retired version is a policy change and
    should be a deliberate edit *here*, not a quiet append to a list.

    ``requires-python`` is deliberately not checked.  Which interpreter CI gates and which
    interpreters the package claims to support are two different numbers; support metadata
    is retired in its own staged round, and coupling them here would force them to move
    together.
    """

    text = _ci_text()
    assert 'python-version: ["3.14"]' in text
    assert "fail-fast: false" in text
    # Neither retired version is gated any more -- as a matrix entry or as a second job.
    assert '"3.12"' not in text
    assert '"3.13"' not in text


def test_ci_runs_the_complete_suite_in_the_gated_job() -> None:
    """No ``-k``, no path subsetting, no marker filter: the whole suite, once."""

    text = _ci_text()
    pytest_steps = re.findall(r"run: python -m pytest[^\n]*", text)
    assert len(pytest_steps) == 1, pytest_steps
    step = pytest_steps[0]
    assert "-n 4" in step
    assert "--dist=loadscope" in step
    assert "--durations=30" in step
    # A cap, not ``0``: ``0`` prints every test and buries the signal.
    assert "--durations=0" not in step
    # Nothing that narrows what runs.
    for narrowing in (" -k ", "-m 'not", "tests/test_", "--last-failed"):
        assert narrowing not in step, narrowing


def test_ci_keeps_every_gate_as_a_separate_step() -> None:
    """The split exists for timing visibility; each gate must still be there."""

    text = _ci_text()
    for step in (
        "name: Run tests",
        "name: Doctor",
        "name: Compile",
        "name: Desktop offscreen self-test",
    ):
        assert step in text, step
    assert "python -m us_quant doctor" in text
    assert "python -m compileall -q src tests" in text
    assert "python desktop_main.py" in text
    assert "python -c \"import PySide6\"" in text


def test_ci_keeps_the_offscreen_environment() -> None:
    """Without it the self-test tries to open a window on a headless runner."""

    text = _ci_text()
    assert "QT_QPA_PLATFORM: offscreen" in text
    assert 'US_QUANT_SELF_TEST: "1"' in text


def test_ci_does_not_run_the_preview() -> None:
    """The 32-artifact preview stays a UI-specific, opt-in gate.

    It is slow by construction -- it waits on real robustness workflows -- and
    running it for every PR would undo the wall-clock this round recovers.  It is
    still required for page, navigation and render-boundary changes, which the
    task instruction states rather than CI enforcing.
    """

    assert "render_desktop_preview" not in _ci_text()


# -- the dependency that enables all of this -----------------------------


def test_xdist_is_a_declared_test_dependency() -> None:
    """CI passes ``-n 4``, so the extra must install it."""

    text = _PYPROJECT.read_text(encoding="utf-8")
    test_extra = text.split("test = [", 1)[1].split("]", 1)[0]
    assert "pytest-xdist" in test_extra


def test_ci_installs_the_test_extra() -> None:
    """The extra is what supplies pytest and xdist on a clean runner."""

    assert '.[desktop,test]' in _ci_text()


# -- the tiers are documented where a developer will look ----------------


def test_the_script_documents_all_three_tiers() -> None:
    """The header is the only place a developer learns the three invocations."""

    # This one *is* about the documentation, so it reads the whole file.
    source = _VERIFY.read_text(encoding="utf-8")
    for needle in (
        ".\\scripts\\verify.ps1",
        "-CoreOnly",
        "-TestPath",
    ):
        assert needle in source, needle
    assert "does **not** mean" in source, (
        "the header must say -CoreOnly is not 'run fewer tests'"
    )


def test_the_script_states_why_there_is_no_impacted_test_guessing() -> None:
    """A wrong guess is a silent hole in a gate, so the absence is on purpose."""

    source = _VERIFY.read_text(encoding="utf-8")
    assert "changed-file-to-test mapping" in source
    for forbidden in (
        "git diff",
        "TestDependencyGraph",
        "ChangedFileTestMapper",
        "SmartTestSelector",
    ):
        assert forbidden not in _verify_text(), forbidden


# -- the historical method ledger must not regrow -------------------------
#
# This round deleted a ~700-line-per-file method-delta ledger that four *service*
# test files each carried a copy of.  The ledger recorded which methods each past
# round added or removed relative to its own base commit, which meant a change to
# any one capability required editing four unrelated service-test files.
#
# Deleting it once is not the same as keeping it gone: the next migration could
# reintroduce the pattern a file at a time, and nothing would notice until the
# cost was paid again.  These guards make the pattern itself the failing thing.

#: The four files that carried the ledger and must not carry it again.
_FORMER_LEDGER_FILES = (
    "test_desktop_backtest_service.py",
    "test_desktop_history_service.py",
    "test_desktop_market_scan_service.py",
    "test_desktop_universe_service.py",
)

#: A module-level name matching any of these is the ledger coming back.
_LEDGER_NAME_PATTERN = re.compile(
    r"^(BASE_COMMIT|LATER_ROUND\w*|FROZEN_\w+|_FROZEN\w*)$"
    r"|(_V2[A-Z]?_?(ADDED|REMOVED|METHODS|NET_ZERO|CHANGED)\w*)$"
)


def _module_level_names(path: pathlib.Path) -> list[str]:
    import ast

    return [
        node.targets[0].id
        for node in ast.parse(path.read_text(encoding="utf-8")).body
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
    ]


@pytest.mark.parametrize("name", _FORMER_LEDGER_FILES)
def test_a_service_test_carries_no_method_delta_ledger(name: str) -> None:
    """No base-commit constants, no per-round method sets, no frozen lists."""

    path = _REPO_ROOT / "tests" / name
    offenders = [
        declared
        for declared in _module_level_names(path)
        if _LEDGER_NAME_PATTERN.search(declared)
    ]
    assert not offenders, (
        f"{name} declares historical ledger constants {offenders}. The "
        f"current-state home for 'is this retired name still on MainWindow?' is "
        f"the owning capability's architecture test."
    )


@pytest.mark.parametrize("name", _FORMER_LEDGER_FILES)
def test_a_service_test_reads_no_base_commit(name: str) -> None:
    """``git show <base>:...`` is the machinery the ledger needed."""

    source = (_REPO_ROOT / "tests" / name).read_text(encoding="utf-8")
    for forbidden in (
        "BASE_COMMIT",
        "_base_source",
        "_require_base",
        "git\", \"show",
        '"git", "show"',
    ):
        assert forbidden not in source, (name, forbidden)


@pytest.mark.parametrize("name", _FORMER_LEDGER_FILES)
def test_a_service_test_declares_no_delta_test(name: str) -> None:
    """The test that consumed the ledger must not return under any spelling."""

    import ast

    tree = ast.parse((_REPO_ROOT / "tests" / name).read_text(encoding="utf-8"))
    declared = {
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    for forbidden in (
        "test_only_the_declared_methods_changed",
        "test_the_frozen_method_is_byte_identical",
        "test_the_other_frozen_modules_are_untouched",
        "test_the_frozen_sibling_modules_are_untouched",
        "test_the_net_zero_methods_exist_in_neither_revision",
        "test_the_collaborators_are_untouched",
    ):
        assert forbidden not in declared, (name, forbidden)


def test_the_retired_ledger_is_not_rehomed_as_a_central_import() -> None:
    """Four copies must not become one central copy that four files import.

    A shared ``desktop_method_history`` module would still require every future
    migration to register what it added and removed, and would still put four
    service test files in the diff.  Centralising a wrong abstraction keeps the
    cost and adds a dependency.
    """

    for forbidden in (
        "tests/desktop_method_history.py",
        "tests/mainwindow_delta_registry.py",
        "tests/architecture_history_support.py",
        "tests/desktop_method_ledger.py",
    ):
        assert not (_REPO_ROOT / forbidden).exists(), forbidden

    module_names = {
        path.stem for path in (_REPO_ROOT / "tests").glob("*.py")
    }
    for forbidden in (
        "desktop_method_history",
        "mainwindow_delta_registry",
        "architecture_history_support",
        "desktop_method_ledger",
    ):
        assert forbidden not in module_names, forbidden
