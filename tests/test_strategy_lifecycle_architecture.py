"""Stage 6-C architecture guards.

The claims asserted here are the ones that make single lifecycle authority
structural rather than a convention: only the controller can mint an
authorization, the legacy gate flag is not consulted anywhere in the transition
path, the evidence-driven targets are exactly the Paper ones, and nothing below
the composition layer can forge its way across the Paper boundary.
"""

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "us_quant"

DOMAIN = SRC / "trading" / "domain" / "strategy_lifecycle.py"
APPLICATION = SRC / "trading" / "application" / "strategy_lifecycle.py"
POLICY_PORT = SRC / "trading" / "ports" / "strategy_lifecycle_repository.py"
REPOSITORY_ADAPTER = (
    SRC / "trading" / "adapters" / "sqlite" / "strategy_lifecycle_repository.py"
)
COMPOSITION = SRC / "trading" / "composition" / "strategy_lifecycle.py"
STRATEGIES = SRC / "trading" / "application" / "strategies.py"

LIFECYCLE_SURFACE = (
    DOMAIN, APPLICATION, POLICY_PORT, REPOSITORY_ADAPTER, COMPOSITION,
)

STATISTICAL_THRESHOLDS = (
    "MAXIMUM_PBO",
    "MINIMUM_DSR_PROBABILITY",
    "MINIMUM_HAC_POSITIVE_PROBABILITY",
    "walk_forward",
    "deflated_sharpe",
)


def _text(path):
    return path.read_text(encoding="utf-8")


def _import_targets(path):
    targets: set[str] = set()
    for node in ast.walk(ast.parse(_text(path))):
        if isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if base:
                targets.add(base)
            for alias in node.names:
                targets.add(f"{base}.{alias.name}" if base else alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                targets.add(alias.name)
    return targets


def _production_files():
    return tuple(
        path for path in SRC.rglob("*.py") if "__pycache__" not in path.parts
    )


# -- single authority ----------------------------------------------------


def test_l01_exactly_one_lifecycle_controller_definition():
    matches = []
    for path in (SRC / "trading").rglob("*.py"):
        tree = ast.parse(_text(path))
        matches.extend(
            (path, node.lineno)
            for node in ast.walk(tree)
            if isinstance(node, ast.ClassDef)
            and node.name == "StrategyLifecycleController"
        )

    assert len(matches) == 1
    assert matches[0][0] == APPLICATION


def test_l02_only_the_controller_can_mint_an_authorization():
    references = [
        path for path in _production_files() if "_CONTROLLER_TOKEN" in _text(path)
    ]

    assert set(references) == {DOMAIN, APPLICATION}


def test_l03_authorization_requires_the_private_token():
    tree = ast.parse(_text(DOMAIN))
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "StrategyLifecycleAuthorization":
            init = next(
                child for child in node.body
                if isinstance(child, ast.FunctionDef) and child.name == "__init__"
            )
            break
    else:
        raise AssertionError("StrategyLifecycleAuthorization is missing")

    assert "_controller_token" in {argument.arg for argument in init.args.kwonlyargs}


# -- the legacy flag is not authority -----------------------------------


def test_l04_the_transition_path_never_consults_gate_passed():
    """The whole point of 6-C: a stored boolean decides nothing.

    Asserted on attribute *reads* inside the method rather than on the file
    text, so the docstring may explain the retirement while any actual
    ``current.gate_passed`` lookup turns this red.
    """

    tree = ast.parse(_text(STRATEGIES))
    transition = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "transition"
    )

    reads = {
        node.attr for node in ast.walk(transition) if isinstance(node, ast.Attribute)
    }

    assert "gate_passed" not in reads
    assert "gate_reason" not in reads


def test_l05_the_evidence_driven_targets_are_exactly_the_paper_ones():
    text = _text(STRATEGIES)
    assert "AUTHORIZATION_REQUIRED_TARGETS" in text

    from us_quant.trading.application.strategies import (
        AUTHORIZATION_REQUIRED_TARGETS,
    )
    from us_quant.trading.domain.strategy import StrategyStatus

    assert AUTHORIZATION_REQUIRED_TARGETS == {
        StrategyStatus.PAPER_SHADOW,
        StrategyStatus.PAUSED,
    }
    # STOPPED stays explicit governance, not an autonomous decision.
    assert StrategyStatus.STOPPED not in AUTHORIZATION_REQUIRED_TARGETS


def test_l06_stopped_is_not_a_lifecycle_action():
    from us_quant.trading.domain.strategy_lifecycle import StrategyLifecycleAction

    assert "STOPPED" not in {action.value for action in StrategyLifecycleAction}
    assert [action.value for action in StrategyLifecycleAction] == [
        "PROMOTE_TO_PAPER_SHADOW",
        "PAUSE",
        "RESUME_PAPER_SHADOW",
    ]


def test_l07_the_lifecycle_surface_never_recomputes_statistics():
    for path in LIFECYCLE_SURFACE:
        text = _text(path)
        for name in STATISTICAL_THRESHOLDS:
            assert name not in text, f"{path.name} names {name}"


# -- dependency direction ------------------------------------------------


def test_l08_application_reaches_only_domain_and_ports():
    targets = _import_targets(APPLICATION)

    assert not any("trading.adapters" in target for target in targets)
    assert not any(target == "sqlite3" for target in targets)
    # The controller no longer names a port itself.  It used to import the trust
    # root's failure type because it re-checked one representative key inline;
    # now it holds a shared coverage-validity validator, which owns the port
    # reads.  So the assertion is that no *adapter* or storage is reachable --
    # the positive half moved to test_l08b.
    assert not any("trading.ports" in target and "adapters" in target for target in targets)


def test_l08b_the_controller_reaches_evidence_only_through_the_shared_validator():
    """The dependency direction this repair establishes, asserted directly.

    The lifecycle controller must not hold a key source, an authentication
    repository or a gate repository of its own: current validity is one service,
    and a controller that could read a single key inline is a controller that can
    drift back to the representative model.
    """

    source = APPLICATION.read_text(encoding="utf-8")
    assert "key_source" not in source
    assert "verification_key" not in source
    assert "coverage_validity" in source
    assert "self._coverage_validity.validate(" in source


def test_l09_domain_stays_free_of_frameworks_and_storage():
    targets = _import_targets(DOMAIN)

    assert not any(
        term in target.lower()
        for target in targets
        for term in ("pyside", "pyqt", "sqlite", "desktop", "ibapi")
    )


def test_l10_composition_is_the_only_place_naming_the_lifecycle_adapter():
    for path in (DOMAIN, APPLICATION, POLICY_PORT):
        assert not any(
            "sqlite.strategy_lifecycle_repository" in target
            for target in _import_targets(path)
        ), path

    assert any(
        "sqlite.strategy_lifecycle_repository" in target
        for target in _import_targets(COMPOSITION)
    )


def test_l11_no_protected_layer_mints_an_authorization():
    """Runtime, broker adapters and desktop must not cross the Paper boundary."""

    protected = (
        SRC / "trading" / "runtime",
        SRC / "trading" / "adapters" / "ibkr",
        SRC / "desktop.py",
        SRC / "desktop_v2",
    )
    files = []
    for root in protected:
        files.extend(root.rglob("*.py") if root.is_dir() else (root,))

    offenders = []
    for path in files:
        if not path.exists():
            continue
        targets = _import_targets(path)
        if any("_CONTROLLER_TOKEN" in target for target in targets):
            offenders.append(str(path.relative_to(SRC)))
        if "StrategyLifecycleAuthorization(" in _text(path):
            offenders.append(str(path.relative_to(SRC)))

    assert offenders == []


def test_l12_lifecycle_never_becomes_execution_authority():
    for path in LIFECYCLE_SURFACE:
        text = _text(path)
        for forbidden in (
            "RiskApplication", "ExecutionApplication", "OrderDispatch",
            "BrokerExecutionPort", "PortfolioRuntime", "submit_order",
        ):
            assert forbidden not in text, f"{path.name} names {forbidden}"


def test_l13_no_ai_or_optimizer_imports_in_the_lifecycle_surface():
    for path in LIFECYCLE_SURFACE:
        targets = _import_targets(path)
        assert not any(
            term in target.lower()
            for target in targets
            for term in ("openai", "anthropic", "optimizer", "llm", "torch")
        ), path


# -- storage hygiene -----------------------------------------------------


def test_l14_the_lifecycle_store_never_alters_the_frozen_schema():
    text = _text(REPOSITORY_ADAPTER)

    for frozen in ("strategy_version", "strategy_deployment"):
        assert f"CREATE TABLE {frozen}" not in text
        assert f"ALTER TABLE {frozen}" not in text
        assert f"DROP TABLE {frozen}" not in text


def test_l15_the_lifecycle_store_is_separate_from_the_other_governance_stores():
    text = _text(REPOSITORY_ADAPTER)

    for other in (
        "strategy_gate_evaluation", "strategy_evidence_authentication",
        "strategy_coverage_evaluation", "strategy_coverage_policy",
    ):
        assert other not in text


def test_l16_the_decision_store_is_crash_safe_by_construction():
    """PREPARED must be written before the transition, and reconciled after."""

    text = _text(APPLICATION)

    assert "PREPARED" in text or "StrategyLifecycleDecisionState.PREPARED" in text
    assert "record_decision" in text
    assert "mark_applied" in text
    assert "mark_superseded" in text
    assert "def reconcile(" in text
    # The decision is persisted before the state machine moves.
    assert text.index("record_decision") < text.index("self._strategies.transition")


def test_l17_the_policy_store_appends_by_compare_and_set():
    text = _text(REPOSITORY_ADAPTER)

    assert "expected_current_revision" in text
    assert "MAX(revision)" in text


def test_l18_lifecycle_paths_do_not_import_the_signing_tool():
    for path in _production_files():
        if "strategy_lifecycle" not in path.name:
            continue
        assert not any(
            "research_evidence_sealing" in target
            for target in _import_targets(path)
        ), path


# -- the Paper launch gate ------------------------------------------------

LAUNCH_GATE = SRC / "trading" / "application" / "paper_authorization.py"
PLAN_BOUNDARY = SRC / "trading" / "application" / "portfolio_plan.py"
PLAN_COMPOSITION = SRC / "trading" / "composition" / "portfolio_operations.py"
PATHS = SRC / "paths.py"

GOVERNED_LAYERS = (
    SRC / "trading" / "runtime",
    SRC / "trading" / "adapters" / "ibkr",
    SRC / "trading" / "application" / "portfolio_runtime.py",
    SRC / "desktop_v2",
)


def test_l19_the_launch_gate_lives_at_the_plan_boundary_only():
    """Instruction 25: not in the runtime, not in risk, not in execution."""

    assert 'self._paper_authorization(' in _text(PLAN_BOUNDARY)

    offenders = []
    for root in GOVERNED_LAYERS:
        files = root.rglob("*.py") if root.is_dir() else (root,)
        for path in files:
            if not path.exists() or "__pycache__" in path.parts:
                continue
            if "paper_authorization" in _text(path):
                offenders.append(str(path.relative_to(SRC)))

    assert offenders == []


def test_l20_the_plan_boundary_no_longer_reads_the_legacy_flag():
    tree = ast.parse(_text(PLAN_BOUNDARY))
    boundary = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef)
        and node.name == "PortfolioOperatingPlanApplication"
    )
    validate = next(
        node
        for node in ast.walk(boundary)
        if isinstance(node, ast.FunctionDef) and node.name == "_validate"
    )

    reads = {
        node.attr for node in ast.walk(validate) if isinstance(node, ast.Attribute)
    }

    assert "gate_passed" not in reads


def test_l21_the_plan_boundary_fails_closed_without_a_gate():
    text = _text(PLAN_BOUNDARY)

    assert "self._paper_authorization is None" in text
    assert "or not self._paper_authorization(" in text


def test_l22_the_authorizer_returns_rather_than_raising():
    """A launch gate that can throw is one a caller wraps in a broad ``except``.

    Asserted **behaviourally**, with adversarial collaborators, because the
    previous structural version of this guard was blind to the failure it was
    meant to catch.  It looked for a literal ``raise`` inside ``authorises`` and
    counted ``return`` statements -- so it passed while the method delegated to
    the current-validity validator, whose own ``ValueError`` (a naive ``now``) and
    ``AttributeError`` (a record of the wrong type) escaped straight through it.
    An exception raised by a *callee* is invisible to an AST scan of the caller.

    Each case below hands the real authorizer a collaborator that misbehaves in
    one specific way.  All of them must yield ``False``.
    """

    from datetime import datetime, timedelta, timezone
    from types import SimpleNamespace

    from us_quant.trading.application.paper_authorization import PaperLaunchAuthorizer
    from us_quant.trading.application.strategy_coverage_validity import (
        StrategyCoverageCurrentValidator,
    )
    from us_quant.trading.domain.evidence_auth import (
        EvidenceAuthenticationVerdict,
    )
    from us_quant.trading.domain.strategy_coverage import (
        StrategyCoverageEvaluation,
        StrategyCoverageItem,
        StrategyCoverageVerdict,
    )
    from us_quant.trading.domain.strategy_gate import StrategyGateVerdict
    from us_quant.trading.domain.strategy_lifecycle import (
        StrategyLifecycleAction,
        StrategyLifecycleDecisionState,
        StrategyLifecyclePolicy,
    )

    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    version_id = "version-1"

    item = StrategyCoverageItem(
        symbol="AAPL", review_run_id="review-a", data_hash="data-a", key_id="key-a",
        authentication_id="auth-a", gate_evaluation_id="gate-a",
        signed_at=now - timedelta(hours=2), generated_at=now - timedelta(hours=1),
    )
    coverage = StrategyCoverageEvaluation(
        evaluation_id="coverage-1", strategy_version_id=version_id,
        strategy_semver="1.0.0", parameter_hash="ph", universe_hash="u", code_hash="c",
        policy_id="coverage-policy-1", policy_revision=1,
        policy_version="evidence-coverage-v1",
        verdict=StrategyCoverageVerdict.PASS, blockers=(),
        items=(item,), covered_symbols=("AAPL",), required_symbols=("AAPL",),
        distinct_review_runs=1, distinct_data_hashes=1,
        evaluator_version="coverage-1", evaluated_at=now - timedelta(minutes=30),
    )

    class _Decisions:
        def decisions_for_version(self, _version_id):
            return (
                SimpleNamespace(
                    state=StrategyLifecycleDecisionState.APPLIED,
                    action=StrategyLifecycleAction.PROMOTE_TO_PAPER_SHADOW,
                    coverage_evaluation_id="coverage-1",
                    policy_id="lifecycle-policy-1", policy_revision=1,
                ),
            )

    class _Coverages:
        def get_evaluation(self, _evaluation_id):
            return coverage

    class _Policies:
        def get_policy(self, policy_id, revision):
            return StrategyLifecyclePolicy(
                policy_id=policy_id, revision=revision,
                policy_version="strategy-lifecycle-v1",
                permitted_actions=(StrategyLifecycleAction.PROMOTE_TO_PAPER_SHADOW,),
                required_gate_policy_version="independent-review-v1",
                required_coverage_policy_version="evidence-coverage-v1",
                maximum_evidence_age=timedelta(days=30), created_at=now,
            )

    class _Authentications:
        def get(self, authentication_id):
            return SimpleNamespace(
                authentication_id=authentication_id, strategy_version_id=version_id,
                review_run_id="review-a", key_id="key-a",
                verdict=EvidenceAuthenticationVerdict.PASS,
            )

    class _Gates:
        def get(self, evaluation_id):
            return SimpleNamespace(
                evaluation_id=evaluation_id, strategy_version_id=version_id,
                review_run_id="review-a", parameter_hash="ph", data_hash="data-a",
                symbol="AAPL", policy_version="independent-review-v1",
                verdict=StrategyGateVerdict.PASS,
            )

    class _Keys:
        def verification_key(self, key_id):
            return SimpleNamespace(key_id=key_id, trust_status="ACTIVE")

    def build(*, clock=lambda: now, authentications=None):
        return PaperLaunchAuthorizer(
            decisions=_Decisions(), coverages=_Coverages(),
            lifecycle_policies=_Policies(),
            coverage_validity=StrategyCoverageCurrentValidator(
                authentications=authentications or _Authentications(),
                gates=_Gates(), key_source=_Keys(),
            ),
            clock=clock,
        )

    # The happy path first, so the adversarial cases below are not passing merely
    # because nothing ever authorises.
    assert build().authorises(version_id) is True

    def _boom():
        raise RuntimeError("a collaborator misbehaved")

    adversarial = {
        "naive clock": dict(clock=lambda: datetime(2026, 1, 1)),
        "None clock": dict(clock=lambda: None),
        "clock raising": dict(clock=_boom),
    }
    for label, kwargs in adversarial.items():
        assert build(**kwargs).authorises(version_id) is False, label

    # A port handing back a record of the wrong shape.
    class _WrongType:
        def get(self, _authentication_id):
            return object()

    assert build(authentications=_WrongType()).authorises(version_id) is False


def test_l32_a_swallowed_failure_is_logged_not_silent():
    """A programming error must not look like a policy decision.

    The wrapper refuses on any exception, and the caller renders a refusal and a
    failure identically (``paper=NOT_AUTHORISED``).  Without a log the two are
    indistinguishable, so a genuine bug would never be investigated -- and the
    fix would have traded a traceback for silence.

    The logging must also not be able to break the contract it protects: a
    handler that raises cannot be allowed to turn a refusal into an exception.
    """

    import logging
    from datetime import datetime, timezone

    from us_quant.trading.application.paper_authorization import PaperLaunchAuthorizer

    class _Exploding:
        def decisions_for_version(self, _version_id):
            raise RuntimeError("a collaborator misbehaved")

    class _Unused:
        pass

    authorizer = PaperLaunchAuthorizer(
        decisions=_Exploding(), coverages=_Unused(),
        lifecycle_policies=_Unused(), coverage_validity=_Unused(),
        clock=lambda: datetime(2026, 1, 1, tzinfo=timezone.utc),
    )

    records: list[logging.LogRecord] = []

    class _Capture(logging.Handler):
        def emit(self, record):
            records.append(record)

    logger = logging.getLogger(
        "us_quant.trading.application.paper_authorization"
    )
    handler = _Capture(level=logging.ERROR)
    logger.addHandler(handler)
    try:
        assert authorizer.authorises("version-1") is False
    finally:
        logger.removeHandler(handler)

    assert records, "the swallowed failure was not logged"

    # A handler that raises must not defeat the wrapper.
    class _Hostile(logging.Handler):
        def emit(self, record):
            raise RuntimeError("the logging handler is broken")

    hostile = _Hostile(level=logging.ERROR)
    logger.addHandler(hostile)
    try:
        assert authorizer.authorises("version-1") is False
    finally:
        logger.removeHandler(hostile)


def test_l23_pause_is_never_an_entry_action():
    from us_quant.trading.application.paper_authorization import PAPER_ENTRY_ACTIONS
    from us_quant.trading.domain.strategy_lifecycle import StrategyLifecycleAction

    assert PAPER_ENTRY_ACTIONS == {
        StrategyLifecycleAction.PROMOTE_TO_PAPER_SHADOW,
        StrategyLifecycleAction.RESUME_PAPER_SHADOW,
    }
    assert StrategyLifecycleAction.PAUSE not in PAPER_ENTRY_ACTIONS


def test_l24_composition_is_the_only_place_that_names_the_authorizer():
    for path in (PLAN_BOUNDARY, LAUNCH_GATE):
        assert not any(
            "adapters" in target for target in _import_targets(path)
        ), path

    assert any(
        "paper_authorization" in target for target in _import_targets(PLAN_COMPOSITION)
    )


def test_l25_the_trust_root_stays_outside_the_runtime_store():
    """B1 requires verification trust material outside the artifact store."""

    tree = ast.parse(_text(PATHS))
    trust = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "trust_root"
    )

    reads = {
        node.attr for node in ast.walk(trust) if isinstance(node, ast.Attribute)
    }

    assert "state_root" in reads
    assert "runtime_root" not in reads


# -- the repair: one validator, and only by exact identity -----------------

VALIDITY = SRC / "trading" / "application" / "strategy_coverage_validity.py"
LIFECYCLE_COMPOSITION = SRC / "trading" / "composition" / "strategy_lifecycle.py"
PLAN_COMPOSITION = SRC / "trading" / "composition" / "portfolio_operations.py"


def test_l26_exactly_one_current_validity_validator_definition():
    """One service answers "is this claim still current", for both boundaries.

    Two definitions -- a second one beside the lifecycle controller, say -- is how
    the launch gate and the promotion start disagreeing about the same claim.
    """

    matches = []
    for path in (SRC / "trading").rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        tree = ast.parse(_text(path))
        matches.extend(
            (path, node.lineno)
            for node in ast.walk(tree)
            if isinstance(node, ast.ClassDef)
            and node.name == "StrategyCoverageCurrentValidator"
        )

    assert len(matches) == 1
    assert matches[0][0] == VALIDITY


def test_l27_only_the_composition_layer_constructs_the_validator():
    """The validator is wired, never built inline by a consumer.

    A controller or launch gate that constructed its own would pick its own
    stores, and "current" would mean whatever that instance happened to read.
    """

    offenders = []
    for path in _production_files():
        if path in (
            PLAN_COMPOSITION,
            LIFECYCLE_COMPOSITION,
            SRC / "trading" / "composition" / "paper_canary_readiness.py",
        ):
            continue
        if "StrategyCoverageCurrentValidator(" in _text(path):
            offenders.append(str(path.relative_to(SRC)))

    assert offenders == []


def test_l28_the_validator_takes_no_representative_argument():
    """``validate`` is about the claim, so it cannot be handed one member.

    The retired model passed a representative ``authenticated`` and ``gate``; a
    validator that accepted one could be used to check a claim through a single
    member, which is the defect this repair removes.
    """

    tree = ast.parse(_text(VALIDITY))
    validator = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef)
        and node.name == "StrategyCoverageCurrentValidator"
    )
    validate = next(
        node
        for node in ast.walk(validator)
        if isinstance(node, ast.FunctionDef) and node.name == "validate"
    )

    parameters = {argument.arg for argument in validate.args.kwonlyargs}
    parameters |= {argument.arg for argument in validate.args.args}
    assert not parameters & {"authenticated", "gate", "authentication", "item"}
    assert "coverage" in parameters
    assert "required_gate_policy_version" in parameters


def test_l29_the_validator_reads_members_by_exact_id_only():
    """No ``latest_for_version`` fallback anywhere in the validity check.

    The item names its own ``authentication_id`` and ``gate_evaluation_id``.  A
    fallback to the newest record for the version would answer "is there some
    passing evidence" while appearing to answer "is *this* evidence still good".
    """

    text = _text(VALIDITY)

    assert "latest_for_version" not in text
    assert "authentications_for_version" not in text
    assert "evaluations_for_version" not in text
    assert "self._authentications.get(item.authentication_id)" in text
    assert "self._gates.get(item.gate_evaluation_id)" in text


def test_l30_the_launch_gate_reads_no_active_policy():
    """The decision's own revision, never "whatever is active now".

    Substituting the active policy would apply today's gate revision and today's
    age bound to a promotion justified under different ones -- and the dangerous
    direction is a laxer revision resurrecting a promotion that was refused.
    """

    text = _text(LAUNCH_GATE)

    assert "active_policy" not in text
    assert "self._lifecycle_policies.get_policy(" in text


def test_l31_the_chain_blocker_mapping_cannot_drift_silently():
    """Every validator blocker is either mapped or explicitly exempt.

    The lifecycle decision records the umbrella ``EVIDENCE_CHAIN_NOT_CURRENT``
    *and* the specific reason, so an operator knows which repair is needed.  This
    guard pins the *classification*: a new validator blocker has to be either
    mapped or declared unmapped, so the two lists cannot drift apart and silently
    degrade the diagnosis.

    It does **not** pin the safety property, and the docstring says so rather than
    implying otherwise.  The refusal comes from the umbrella blocker being added
    unconditionally, which is asserted where that ordering lives --
    ``test_the_chain_blocker_is_added_unconditionally`` in this module.  Removing
    the umbrella leaves this guard green, so a reader must not treat it as the
    thing that prevents a fail-open.

    The ``deliberately_unmapped`` set is a decision, not a proof: a developer can
    add a reason to it.  That is acceptable only because the umbrella refuses
    regardless -- what the set can do is lose a diagnosis, never an authorisation.
    """

    from us_quant.trading.application.strategy_coverage_validity import (
        StrategyCoverageValidityBlocker,
    )
    from us_quant.trading.application.strategy_lifecycle import (
        _CHAIN_BLOCKER_BY_VALIDITY,
    )

    #: Reasons that are deliberately not translated.  Each is either structural
    #: (reported by the lifecycle's own checks) or has no distinct lifecycle
    #: vocabulary, and the umbrella plus the coverage verdict already says enough.
    deliberately_unmapped = {
        StrategyCoverageValidityBlocker.COVERAGE_NOT_PASSED,
        StrategyCoverageValidityBlocker.MEMBER_IDENTITY_MISMATCH,
        StrategyCoverageValidityBlocker.DUPLICATE_MEMBER_IDENTITY,
        StrategyCoverageValidityBlocker.GATE_POLICY_MISMATCH,
        StrategyCoverageValidityBlocker.FUTURE_MEMBER_TIMESTAMP,
    }

    classified = set(_CHAIN_BLOCKER_BY_VALIDITY) | deliberately_unmapped
    every_reason = set(StrategyCoverageValidityBlocker)

    assert every_reason - classified == set(), sorted(
        reason.value for reason in every_reason - classified
    )
    # And nothing is mapped that is also declared unmapped.
    assert set(_CHAIN_BLOCKER_BY_VALIDITY) & deliberately_unmapped == set()

    # Every target is a real lifecycle blocker, so a rename cannot leave a
    # dangling reference that only fails at runtime.
    from us_quant.trading.domain.strategy_lifecycle import StrategyLifecycleBlocker

    for reason, target in _CHAIN_BLOCKER_BY_VALIDITY.items():
        assert isinstance(target, StrategyLifecycleBlocker), reason


def test_the_chain_blocker_is_added_unconditionally():
    """The safety property F3 rests on: an untranslatable reason still refuses.

    ``_chain_failures`` adds ``EVIDENCE_CHAIN_NOT_CURRENT`` *before* the mapped
    union and regardless of it, so a validator blocker this codebase has never
    seen cannot produce an empty failure set.  Asserted on the AST rather than by
    behaviour, because the property is about ordering inside one function.

    Three things are pinned:

    * the branch is taken when the claim is **invalid** -- asserted
      *behaviourally*, by driving the real controller with a stub validator and
      checking that the umbrella appears for an INVALID result and not for a VALID
      one.  A text match on the condition would have been both too strong (it
      rejects a correct ``is False`` spelling) and too weak (it cannot tell
      ``not validity.valid`` from ``not validity.valid or True``);
    * the umbrella ``add`` is present and unconditional;
    * it precedes the union that consults the translation table.

    L31 stays green when any of these break, which is why the two guards are
    separate.
    """

    tree = ast.parse(_text(APPLICATION))
    chain = next(
        (
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "_chain_failures"
        ),
        None,
    )
    assert chain is not None, "_chain_failures is missing"

    candidates = [
        node
        for node in chain.body
        if isinstance(node, ast.If) and "validity.valid" in ast.unparse(node.test)
    ]
    assert candidates, (
        "no branch in _chain_failures tests `validity.valid`; the chain validity "
        "result is not consulted"
    )
    guarded = candidates[0]

    # Ordering: the umbrella must be an unconditional add inside that branch, and
    # it must come before the union that consults the translation table.
    statements = [ast.unparse(node) for node in guarded.body]
    umbrella = [
        index
        for index, text in enumerate(statements)
        if "failures.add(StrategyLifecycleBlocker.EVIDENCE_CHAIN_NOT_CURRENT)" in text
    ]
    union = [
        index
        for index, text in enumerate(statements)
        if "_CHAIN_BLOCKER_BY_VALIDITY" in text
    ]

    assert umbrella, (
        "the umbrella blocker is not added unconditionally inside the "
        "chain-validity branch"
    )
    assert union, "the translation table is not consulted inside that branch"
    assert umbrella[0] < union[0], (
        "the umbrella must be added before the mapped union, so an unmapped "
        "reason cannot leave the failure set empty"
    )

    # Polarity, behaviourally: an INVALID claim must produce the umbrella and a
    # VALID one must not.  This is what catches `if validity.valid:`, and unlike a
    # text match it accepts any correct spelling of the condition.
    _assert_chain_polarity()


def _assert_chain_polarity() -> None:
    """Drive `_chain_failures` with a stub validator and check the branch.

    The controller is constructed with a validator whose `validate` returns a
    chosen result, so the branch is exercised rather than read.  An INVALID result
    must add the umbrella; a VALID one must not.  A condition written as
    `if validity.valid:` inverts exactly that, and any equivalent-but-differently
    spelled correct condition still passes.
    """

    from datetime import datetime, timedelta, timezone

    from us_quant.trading.application.strategy_coverage_validity import (
        StrategyCoverageValidityBlocker,
        StrategyCoverageValidityResult,
        StrategyCoverageValidityVerdict,
    )
    from us_quant.trading.application.strategy_lifecycle import (
        StrategyLifecycleController,
    )
    from us_quant.trading.domain.strategy import (
        StrategyDefinition,
        StrategyIdentity,
        StrategyMode,
        StrategyStatus,
        StrategyVersion,
    )
    from us_quant.trading.domain.strategy_coverage import (
        StrategyCoverageEvaluation,
        StrategyCoverageItem,
        StrategyCoverageVerdict,
    )
    from us_quant.trading.domain.strategy_lifecycle import (
        StrategyLifecycleAction,
        StrategyLifecycleBlocker,
        StrategyLifecyclePolicy,
    )
    from decimal import Decimal

    now = datetime(2026, 1, 1, tzinfo=timezone.utc)

    class _StubValidator:
        def __init__(self, result):
            self._result = result

        def validate(self, coverage, **_kwargs):
            return self._result

    invalid = StrategyCoverageValidityResult(
        verdict=StrategyCoverageValidityVerdict.INVALID,
        blockers=(StrategyCoverageValidityBlocker.STALE_MEMBER,),
    )
    valid = StrategyCoverageValidityResult(
        verdict=StrategyCoverageValidityVerdict.VALID, blockers=()
    )

    version = StrategyVersion(
        definition=StrategyDefinition("family-1", "Example", "test"),
        identity=StrategyIdentity("family-1", "version-1", "params-1"),
        semver="1.0.0",
        status=StrategyStatus.RESEARCH,
        mode=StrategyMode.RESEARCH,
        parameters={"period": 5},
        universe_hash="universe-1",
        code_hash="code-1",
        risk_budget_pct=Decimal("0.01"),
        gate_passed=False,
        gate_reason="legacy",
        created_at=now,
        updated_at=now,
    )
    policy = StrategyLifecyclePolicy(
        policy_id="lifecycle-policy-1", revision=1,
        policy_version="strategy-lifecycle-v1",
        permitted_actions=(StrategyLifecycleAction.PROMOTE_TO_PAPER_SHADOW,),
        required_gate_policy_version="independent-review-v1",
        required_coverage_policy_version="evidence-coverage-v1",
        maximum_evidence_age=timedelta(days=30), created_at=now,
    )
    coverage = StrategyCoverageEvaluation(
        evaluation_id="coverage-1", strategy_version_id="version-1",
        strategy_semver="1.0.0", parameter_hash="params-1",
        universe_hash="universe-1", code_hash="code-1",
        policy_id="coverage-policy-1", policy_revision=1,
        policy_version="evidence-coverage-v1",
        verdict=StrategyCoverageVerdict.PASS, blockers=(),
        # A PASS claim must name at least one admitted item, so the structural
        # checks ahead of the branch under test see a well-formed claim and do not
        # short-circuit the comparison.
        items=(StrategyCoverageItem(
            symbol="AAPL", review_run_id="review-a", data_hash="data-a",
            key_id="key-a", authentication_id="auth-a",
            gate_evaluation_id="gate-a",
            signed_at=now - timedelta(hours=2),
            generated_at=now - timedelta(hours=1),
        ),),
        covered_symbols=("AAPL",), required_symbols=("AAPL",),
        distinct_review_runs=1, distinct_data_hashes=1,
        evaluator_version="coverage-1", evaluated_at=now,
    )

    def failures_for(result):
        controller = StrategyLifecycleController(
            coverage_validity=_StubValidator(result)
        )
        return controller._chain_failures(
            version=version, policy=policy, coverage=coverage, decided_at=now
        )

    umbrella = StrategyLifecycleBlocker.EVIDENCE_CHAIN_NOT_CURRENT

    assert umbrella in failures_for(invalid), (
        "an INVALID chain-validity result did not produce "
        "EVIDENCE_CHAIN_NOT_CURRENT; the branch is inverted (fail-open)"
    )
    assert umbrella not in failures_for(valid), (
        "a VALID chain-validity result produced EVIDENCE_CHAIN_NOT_CURRENT; "
        "the branch is inverted the other way (false refusal)"
    )
