"""Guards for the project-level OpenCodeReview rules.

``.opencodereview/rule.json`` is the only thing that tells the advisory reviewer
what this project considers a defect.  It is not covered by any other gate: a
rule file that becomes invalid JSON, loses the trading rule, or lists its
patterns in the wrong order is silently ignored by OCR -- the review still runs,
still reports "clean", and the layer this round exists to add is gone without a
single failure anywhere.

So these guards assert the properties that make the file *work*, not its text:

* it parses, and every entry has a usable ``path`` and ``rule``;
* the architecture intents this round promised to cover are still covered, by
  path and by the vocabulary of the rule text (never by the whole sentence, so a
  legitimate rewording does not fail here);
* declaration order still puts specific paths before catch-alls.  OCR resolves
  rules **first match wins**, so a reordered file changes what every file is
  reviewed against while looking perfectly fine;
* ``include`` still keeps ``tests/**`` reviewable.  Measured on this repository
  against one unchanged diff: with no rule file, **0** test files reach the
  reviewable list; with the ``include`` entry, **10**.  OCR's built-in default
  excludes contain ``**/test_*.py``, and every test file here is
  ``tests/test_*.py``, so dropping that one line quietly removes test review
  entirely;
* the round's own boundaries still hold: no credentials in the rule file, no
  reviewer wired into CI, review output still ignored by git, and the wrapper
  still propagates the exit code of the command it wraps.

What these guards deliberately do not do: pin the rule sentences.  The rule text
is a prompt, and improving a prompt is the normal way to work on this file.  Each
check below therefore names the *intent* it protects -- the path that must have a
rule, and a couple of words any faithful version of that rule still contains.
"""

from __future__ import annotations

import json
import pathlib
import re
from typing import Any

import pytest

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
_RULE_FILE = _REPO_ROOT / ".opencodereview" / "rule.json"
_REVIEW_PS1 = _REPO_ROOT / "scripts" / "review.ps1"
_GITIGNORE = _REPO_ROOT / ".gitignore"
_CI = _REPO_ROOT / ".github" / "workflows" / "ci.yml"
_DOC = _REPO_ROOT / "docs" / "CODE_REVIEW.md"

#: The path OCR must use for the project layer.  Pinned literally because it is
#: the one contract that is not ours to choose.
_PROJECT_RULE_PATH = ".opencodereview/rule.json"

#: The catch-all every more specific ``src/`` rule has to be declared before.
_GLOBAL_PYTHON_PATTERN = "src/us_quant/**/*.py"

#: ``intent -> (path pattern that must exist, tokens its rule text must contain)``.
#:
#: One entry per area the round promised to cover.  The tokens are chosen to
#: survive rewording: they are the nouns the rule cannot stop talking about
#: without ceasing to be about that area.
_REQUIRED_INTENTS: dict[str, tuple[str, tuple[str, ...]]] = {
    "trading safety": ("src/us_quant/trading/application/risk.py", ("decimal", "reject")),
    "paper lifecycle": ("src/us_quant/trading/application/paper/**", ("halted", "reconcil")),
    "concurrency": ("src/us_quant/trading/runtime/**", ("lock", "inflight")),
    "desktop architecture": (
        "src/us_quant/{desktop.py,desktop_v2/**}",
        ("projector", "page"),
    ),
    "tests": ("tests/**", ("mutation", "assert")),
    "ci security": (".github/**", ("pull_request_target", "secret")),
    "global python": (_GLOBAL_PYTHON_PATTERN, ("datetime", "decimal")),
}

#: Keys the rule schema defines.  An unknown top-level key is either a typo or a
#: field this OCR version ignores, and both mean a rule that never runs.
_ALLOWED_TOP_LEVEL_KEYS = frozenset({"include", "exclude", "rules"})

#: Credential-shaped literals.  Matching *values* only, and only these shapes, so
#: that the CI rule may keep saying "secrets must not reach logs" -- which is the
#: whole point of that rule -- without tripping this check.
_SECRET_LITERAL = re.compile(
    r"sk-[A-Za-z0-9]{16,}"
    r"|gh[pousr]_[A-Za-z0-9]{20,}"
    r"|github_pat_[A-Za-z0-9_]{20,}"
    r"|AKIA[0-9A-Z]{16}"
    r"|xox[baprs]-[A-Za-z0-9-]{10,}"
)

#: Key names that would mean a credential was configured in the repository.
_CREDENTIAL_KEY = re.compile(
    r"^(api[_-]?key|token|access[_-]?token|auth[_-]?token|password|"
    r"secret|client[_-]?secret|authorization|credential|credentials)$",
    re.IGNORECASE,
)


def _rule_spec() -> dict[str, Any]:
    """The parsed document, or a clean named failure.

    ``pytest.fail`` rather than a bare ``json.loads``: a helper that raises
    ``JSONDecodeError`` reports as a traceback, and the reader has to reconstruct
    which file was being read and why it matters.  Every test that needs the spec
    now fails with the same sentence.
    """

    try:
        spec = json.loads(_RULE_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        pytest.fail(f"{_PROJECT_RULE_PATH} is missing")
    except json.JSONDecodeError as error:
        pytest.fail(f"{_PROJECT_RULE_PATH} is not valid JSON: {error}")
    if not isinstance(spec, dict):
        pytest.fail(f"{_PROJECT_RULE_PATH} must contain a JSON object, found {type(spec).__name__}")
    return spec


def _rule_indices() -> list[int]:
    """Indices to parametrize ``test_every_rule_entry_is_usable`` over.

    Defensive on purpose, because this runs at **collection** time: an exception
    here does not fail one test, it aborts the whole file.  Measured by hand --
    with ``rule.json`` replaced by invalid JSON the naive version reported
    ``1 error during collection`` and **0** tests executed, so the guards for CI,
    ``.gitignore`` and the docs never ran either, and the file that exists to
    explain a broken rule file explained nothing.

    Falling back to a single index keeps the dedicated tests in charge of saying
    what is wrong: ``test_the_project_rule_file_is_valid_json`` and the
    per-entry test both now fail with the parse error itself.
    """

    try:
        spec = json.loads(_RULE_FILE.read_text(encoding="utf-8"))
        rules = spec["rules"]
    except Exception:
        return [0]
    if not isinstance(rules, list) or not rules:
        return [0]
    return list(range(len(rules)))


def _rules() -> list[dict[str, Any]]:
    rules = _rule_spec().get("rules")
    if not isinstance(rules, list):
        pytest.fail(f"{_PROJECT_RULE_PATH} must declare a 'rules' array")
    return rules


def _pattern_to_index() -> dict[str, int]:
    return {entry["path"]: index for index, entry in enumerate(_rules())}


def _rule_text(pattern: str) -> str:
    for entry in _rules():
        if entry["path"] == pattern:
            return entry["rule"]
    raise AssertionError(
        f"no rule declares {pattern!r}; that area is no longer reviewed against "
        f"anything project-specific"
    )


def _walk_keys(value: Any) -> list[str]:
    """Every object key anywhere in the parsed document."""

    if isinstance(value, dict):
        keys = list(value)
        for nested in value.values():
            keys.extend(_walk_keys(nested))
        return keys
    if isinstance(value, list):
        keys = []
        for nested in value:
            keys.extend(_walk_keys(nested))
        return keys
    return []


# -- the file is usable at all -------------------------------------------


def test_the_project_rule_file_exists() -> None:
    """At the path OCR looks in, spelled exactly as OCR spells it."""

    assert _RULE_FILE.is_file(), (
        f"{_PROJECT_RULE_PATH} is missing. Without it OCR falls through to the "
        f"global and system layers and reviews this project against generic "
        f"language rules only."
    )


def test_the_project_rule_file_is_valid_json() -> None:
    """A parse error here is silent: OCR skips the layer, it does not fail."""

    spec = _rule_spec()
    assert isinstance(spec, dict), type(spec)


def test_the_rule_file_declares_only_schema_keys() -> None:
    """An unknown top-level key is a rule that never runs."""

    unknown = sorted(set(_rule_spec()) - _ALLOWED_TOP_LEVEL_KEYS)
    assert not unknown, (
        f"unknown top-level key(s) {unknown}; the rule schema defines only "
        f"{sorted(_ALLOWED_TOP_LEVEL_KEYS)}"
    )


def test_the_rule_list_is_not_empty() -> None:
    rules = _rules()
    assert isinstance(rules, list)
    assert rules, "the project rule layer declares no rules at all"


@pytest.mark.parametrize("index", _rule_indices())
def test_every_rule_entry_is_usable(index: int) -> None:
    """``path`` and ``rule`` are both required, and both must carry content."""

    entry = _rules()[index]
    assert isinstance(entry, dict), entry
    path = entry.get("path")
    text = entry.get("rule")
    assert isinstance(path, str) and path.strip(), f"entry {index} has no path: {entry!r}"
    assert isinstance(text, str) and text.strip(), f"entry {index} has no rule text: {entry!r}"
    if "merge_system_rule" in entry:
        assert isinstance(entry["merge_system_rule"], bool), (
            f"entry {index} sets merge_system_rule to {entry['merge_system_rule']!r}; "
            f"OCR reads it as a boolean"
        )


def test_no_rule_entry_is_declared_twice() -> None:
    """Two entries with the same path means the second one is dead weight.

    First match wins, so the later entry is unreachable.  It looks like coverage
    and is not.
    """

    seen: dict[str, int] = {}
    for index, entry in enumerate(_rules()):
        pattern = entry["path"]
        assert pattern not in seen, (
            f"{pattern!r} is declared at index {seen.get(pattern)} and again at "
            f"{index}; OCR stops at the first match, so the later entry never applies"
        )
        seen[pattern] = index


# -- the promised areas are still covered --------------------------------


@pytest.mark.parametrize("intent", sorted(_REQUIRED_INTENTS))
def test_each_promised_area_still_has_a_rule(intent: str) -> None:
    """Path coverage, one area at a time, with the failing area named."""

    pattern, _tokens = _REQUIRED_INTENTS[intent]
    assert pattern in _pattern_to_index(), (
        f"the {intent!r} rule ({pattern!r}) is gone; that area would fall through "
        f"to a broader rule"
    )


@pytest.mark.parametrize("intent", sorted(_REQUIRED_INTENTS))
def test_each_promised_area_still_says_what_it_is_about(intent: str) -> None:
    """Vocabulary coverage: enough to prove the intent survived a rewrite.

    Deliberately shallow.  This is not a proofread of the prompt -- it fails only
    when the rule stopped being about the thing it exists for.
    """

    pattern, tokens = _REQUIRED_INTENTS[intent]
    text = _rule_text(pattern).lower()
    missing = [token for token in tokens if token not in text]
    assert not missing, (
        f"the {intent!r} rule ({pattern!r}) no longer mentions {missing}. If the "
        f"area is still covered, update _REQUIRED_INTENTS here; if it is not, the "
        f"round's coverage claim is no longer true."
    )


# -- declaration order is behavioural -------------------------------------


def test_specific_paths_are_declared_before_the_catch_all() -> None:
    """``first match wins``, so a catch-all declared early shadows everything.

    Every ``src/us_quant`` rule except the catch-all itself must sit *above* it.
    Reversing two entries in the JSON looks harmless and silently changes what
    the whole source tree is reviewed against.
    """

    order = _pattern_to_index()
    catch_all = order[_GLOBAL_PYTHON_PATTERN]
    offenders = {
        pattern: index
        for pattern, index in order.items()
        if pattern != _GLOBAL_PYTHON_PATTERN and pattern.startswith("src/us_quant")
        and index > catch_all
    }
    assert not offenders, (
        f"{offenders} are declared after the {_GLOBAL_PYTHON_PATTERN!r} catch-all "
        f"(index {catch_all}). OCR applies the first matching pattern, so these "
        f"rules would never run."
    )


def test_trading_core_is_declared_before_the_catch_all() -> None:
    """The same property, stated for the rule that carries trading safety."""

    order = _pattern_to_index()
    assert "src/us_quant/trading/**" in order, "the trading core rule is gone"
    assert order["src/us_quant/trading/**"] < order[_GLOBAL_PYTHON_PATTERN]


def test_the_narrow_trading_rules_are_declared_before_the_umbrella() -> None:
    """``trading/**`` must not shadow ``risk.py`` / ``execution.py`` / ``paper/**``.

    ``trading/**`` is the broadest trading rule and therefore has to come last
    among them, immediately before the global catch-all.
    """

    order = _pattern_to_index()
    umbrella = order["src/us_quant/trading/**"]
    for narrow in (
        "src/us_quant/trading/runtime/workflow_state.py",
        "src/us_quant/trading/application/risk.py",
        "src/us_quant/trading/application/execution.py",
        "src/us_quant/trading/application/paper/**",
        "src/us_quant/trading/runtime/**",
        "src/us_quant/trading/adapters/**",
    ):
        assert narrow in order, f"{narrow!r} is gone"
        assert order[narrow] < umbrella, (
            f"{narrow!r} (index {order[narrow]}) is declared after the broader "
            f"'src/us_quant/trading/**' (index {umbrella}) and would never apply"
        )


# -- the filter keeps tests reviewable ------------------------------------


def test_tests_are_kept_reviewable_by_include() -> None:
    """The measured difference between reviewing tests and not.

    OCR's built-in default excludes contain ``**/test_*.py``.  Every test file in
    this repository is ``tests/test_*.py``, so without this entry **no** test file
    reaches the reviewer -- measured as 0 reviewable test files without the rule
    file and 10 with it, over one unchanged diff.
    """

    include = _rule_spec().get("include", [])
    assert isinstance(include, list) and include, (
        "rule.json declares no include patterns; OCR's default excludes drop "
        "every tests/test_*.py file from review"
    )
    assert any(pattern.startswith("tests/") for pattern in include), (
        f"no include pattern covers tests/: {include}. The test rule would then "
        f"be declared but never applied."
    )


def test_review_output_is_excluded_from_review() -> None:
    """A stale review artifact must not become input to the next review."""

    exclude = _rule_spec().get("exclude", [])
    assert any(".ocr-output" in pattern for pattern in exclude), exclude


# -- the round's boundaries ----------------------------------------------


def test_the_rule_file_contains_no_credential() -> None:
    """Rules are prompts.  A key in here is a key in the repository."""

    spec = _rule_spec()
    offenders = [
        key for key in _walk_keys(spec) if _CREDENTIAL_KEY.match(key)
    ]
    assert not offenders, (
        f"{offenders} look like a credential configured in the rule file. OCR "
        f"credentials belong in the user's ~/.opencodereview, never in the repo."
    )


def test_the_rule_file_contains_no_secret_literal() -> None:
    """The prose may say 'secret'; a value may not *be* one."""

    raw = _RULE_FILE.read_text(encoding="utf-8")
    match = _SECRET_LITERAL.search(raw)
    assert match is None, f"rule.json contains a credential-shaped literal near {match!r}"


def test_ci_does_not_wire_the_reviewer_yet() -> None:
    """The reviewer is advisory, so no required gate may depend on it.

    This is the deliberate place to change when the GitHub Action lands in its own
    round.  Until then, an OCR entry in the workflow -- or an ``OCR_LLM_*`` secret
    it would read -- means the advisory layer became a merge gate without the
    evaluation period the round called for.
    """

    text = _CI.read_text(encoding="utf-8")
    for forbidden in ("ocr", "open-code-review", "opencodereview", "ocr_llm"):
        assert not re.search(rf"\b{re.escape(forbidden)}\b", text, re.IGNORECASE), (
            f"ci.yml mentions {forbidden!r}; the reviewer is advisory and must not "
            f"gate a pull request yet"
        )


def test_review_output_is_ignored_by_git() -> None:
    """Root-anchored, for the same reason ``/runtime/`` is.

    An unanchored ``.ocr-output/`` could shadow a real source directory name.
    """

    text = _GITIGNORE.read_text(encoding="utf-8")
    assert "/.ocr-output/" in text, (
        ".ocr-output/ is not gitignored; a review run writes absolute local paths "
        "and a timestamped tree that must not be committed"
    )


def test_the_wrapper_propagates_the_wrapped_exit_code() -> None:
    """A wrapper that swallowed the exit code would fake a clean review."""

    assert _REVIEW_PS1.is_file(), "scripts/review.ps1 is missing"
    text = _REVIEW_PS1.read_text(encoding="utf-8")
    assert re.search(r"exit\s+\$reviewCode", text), (
        "review.ps1 does not exit with the wrapped command's code"
    )
    # It has to *read* the native exit code before it can propagate it.
    assert text.count("$LASTEXITCODE") >= 2, text.count("$LASTEXITCODE")


def test_the_wrapper_holds_no_credential() -> None:
    """It must not smuggle a provider key into the repository."""

    assert _REVIEW_PS1.is_file(), "scripts/review.ps1 is missing"
    assert _SECRET_LITERAL.search(_REVIEW_PS1.read_text(encoding="utf-8")) is None


def test_every_external_tool_is_located_before_it_is_invoked() -> None:
    """``& missing-command`` throws; it does not return a non-zero exit code.

    Measured on this script by hand, with ``git`` removed from PATH: ``$raw = &
    git --version`` raises ``CommandNotFoundException`` and, under
    ``$ErrorActionPreference = "Stop"``, terminates the script.  A guard written
    as ``if ($LASTEXITCODE -ne 0)`` *after* that line is therefore unreachable,
    and the user gets a PowerShell error record instead of the sentence naming
    what to install -- which is the failure mode the ``scripts/**`` rule in
    ``rule.json`` calls out.

    So both external tools are located with ``Get-Command`` first.  Asserted as
    the presence of that lookup for each, not as a line count.
    """

    text = _REVIEW_PS1.read_text(encoding="utf-8")
    for tool in ("ocr", "git"):
        assert f"Get-Command {tool}" in text, (
            f"review.ps1 does not locate {tool!r} with Get-Command before using "
            f"it, so a missing {tool} fails with a stack trace instead of a message"
        )


def test_the_wrapper_defaults_to_the_delegated_review() -> None:
    """Delegation is the default so that no provider credential is required."""

    text = _REVIEW_PS1.read_text(encoding="utf-8")
    assert "[string]$Mode = \"delegate\"" in text, (
        "review.ps1 no longer defaults to delegate mode, so a bare run would "
        "require an OCR provider configuration"
    )


def test_the_review_document_states_the_advisory_position() -> None:
    """The one sentence every reader has to be able to find verbatim."""

    text = _DOC.read_text(encoding="utf-8")
    assert "OpenCodeReview findings are advisory during evaluation period." in text, (
        "docs/CODE_REVIEW.md must state the advisory position in this exact form"
    )


def test_the_review_document_names_the_three_layers() -> None:
    """Deterministic gates, the reviewer, and the human: all three, by name."""

    text = _DOC.read_text(encoding="utf-8")
    for layer in ("Deterministic gates", "OpenCodeReview", "Manual architecture review"):
        assert layer in text, layer


def test_the_review_document_records_the_workflow() -> None:
    """The loop the next rounds are supposed to follow."""

    text = _DOC.read_text(encoding="utf-8")
    for step in (
        "implementation",
        "targeted verification",
        "OpenCodeReview",
        "Python 3.14 full CI",
        "final PR review",
    ):
        assert step in text, step
