"""Stage 6-B1 architecture guards.

These assert the *shape* of the trust boundary, not just its behaviour.  The
load-bearing claims: the runtime verifies and can never sign, signing is not
reachable from any trading or desktop module, the authenticator is the only
thing that can mint authenticated evidence, and authentication stays
independent of both the gate and the lifecycle.
"""

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "us_quant"

DOMAIN = SRC / "trading" / "domain" / "evidence_auth.py"
APPLICATION = SRC / "trading" / "application" / "evidence_authentication.py"
SIGNATURE_ADAPTER = SRC / "trading" / "adapters" / "evidence_signature.py"
TRUST_STORE_ADAPTER = SRC / "trading" / "adapters" / "evidence_trust_store.py"
REPOSITORY_ADAPTER = (
    SRC / "trading" / "adapters" / "sqlite" / "evidence_authentication_repository.py"
)
REPOSITORY_PORT = SRC / "trading" / "ports" / "evidence_authentication_repository.py"
VERIFICATION_PORT = SRC / "trading" / "ports" / "evidence_verification.py"
ARTIFACT_PORT = SRC / "trading" / "ports" / "research_evidence_artifact.py"
COMPOSITION = SRC / "trading" / "composition" / "evidence_authentication.py"
SEALING = SRC / "research_evidence_sealing.py"

B1_SURFACE = (
    DOMAIN, APPLICATION, SIGNATURE_ADAPTER, TRUST_STORE_ADAPTER,
    REPOSITORY_ADAPTER, REPOSITORY_PORT, VERIFICATION_PORT, ARTIFACT_PORT,
    COMPOSITION,
)


def _text(path):
    return path.read_text(encoding="utf-8")


def _imports(path):
    """Every imported module path and imported name, ignoring prose."""

    tree = ast.parse(_text(path))
    modules: list[str] = []
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            modules.append(node.module or "")
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
    return tuple(modules), tuple(names)


def _imported_modules(path):
    return _imports(path)[0]


def _imported_names(path):
    return _imports(path)[1]


def _import_targets(path):
    """Every dotted path an import can reach, in either import form.

    ``import a.b`` and ``from a import b`` reach the same module, but only the
    first puts ``b`` in the module position -- ``from a import b`` names it as
    an alias.  A guard that reads only module names is therefore trivially
    bypassed by switching import style, so both forms are resolved here.
    """

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


def test_b01_exactly_one_authentication_application_definition():
    matches = []
    for path in (SRC / "trading").rglob("*.py"):
        tree = ast.parse(_text(path))
        matches.extend(
            (path, node.lineno)
            for node in ast.walk(tree)
            if isinstance(node, ast.ClassDef)
            and node.name == "EvidenceAuthenticationApplication"
        )

    assert len(matches) == 1
    assert matches[0][0] == APPLICATION


def test_b02_exactly_one_signature_verifier_definition():
    matches = []
    for path in (SRC / "trading").rglob("*.py"):
        tree = ast.parse(_text(path))
        matches.extend(
            (path, node.lineno)
            for node in ast.walk(tree)
            if isinstance(node, ast.ClassDef)
            and node.name == "Ed25519EvidenceSignatureVerifier"
        )

    assert len(matches) == 1
    assert matches[0][0] == SIGNATURE_ADAPTER


def test_b03_only_the_authenticator_can_mint_authenticated_evidence():
    """The construction token must not leak beyond the one authorised caller."""

    references = [
        path
        for path in _production_files()
        if "_AUTHENTICATOR_TOKEN" in _text(path)
    ]

    assert set(references) == {DOMAIN, APPLICATION}


def test_b04_authenticated_evidence_cannot_be_constructed_without_the_token():
    tree = ast.parse(_text(DOMAIN))
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "AuthenticatedStrategyResearchEvidence":
            init = next(
                child for child in node.body
                if isinstance(child, ast.FunctionDef) and child.name == "__init__"
            )
            break
    else:
        raise AssertionError("AuthenticatedStrategyResearchEvidence is missing")

    assert "_authenticator_token" in {
        argument.arg for argument in init.args.kwonlyargs
    }


# -- the runtime verifies and never signs --------------------------------


def test_b05_no_trading_module_can_sign():
    """No private-key primitive may appear anywhere under the trading package."""

    offenders = [
        str(path.relative_to(SRC))
        for path in (SRC / "trading").rglob("*.py")
        if "Ed25519PrivateKey" in _text(path)
        or "PrivateFormat" in _text(path)
    ]

    assert offenders == []


def test_b06_signature_adapter_imports_only_public_verification_primitives():
    modules = _imported_modules(SIGNATURE_ADAPTER)
    names = _imported_names(SIGNATURE_ADAPTER)

    assert any("Ed25519PublicKey" in name for name in names)
    assert not any("Private" in name for name in names)
    assert not any("Private" in module for module in modules)
    assert "sign" not in {
        node.func.attr
        for node in ast.walk(ast.parse(_text(SIGNATURE_ADAPTER)))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }


def test_b07_cryptography_is_confined_to_the_two_expected_modules():
    offenders = [
        str(path.relative_to(SRC))
        for path in _production_files()
        if any("cryptography" in target for target in _import_targets(path))
        and path not in {SIGNATURE_ADAPTER, SEALING}
    ]

    assert offenders == []


def test_b08_composition_binds_no_signer_and_no_private_key():
    text = _text(COMPOSITION)

    assert "EvidenceAuthenticationApplication(" in text
    assert "SQLiteEvidenceAuthenticationRepository(database_path)" in text
    for forbidden in (
        "Ed25519PrivateKey", "research_evidence_sealing", "sign(", "private_key",
        "RiskApplication", "ExecutionApplication", "PortfolioRuntime",
        "OrderDispatch", "BrokerExecutionPort",
    ):
        assert forbidden not in text


# -- signing is unreachable from trading and desktop ---------------------


def test_b09_nothing_in_src_imports_the_signing_tool():
    offenders = [
        str(path.relative_to(SRC))
        for path in _production_files()
        if path != SEALING
        and any(
            "research_evidence_sealing" in target
            for target in _import_targets(path)
        )
    ]

    assert offenders == []


def test_b10_protected_roots_do_not_reach_the_sealing_module():
    protected_roots = (
        SRC / "trading" / "application",
        SRC / "trading" / "adapters" / "ibkr",
        SRC / "trading" / "runtime",
        SRC / "trading" / "composition",
        SRC / "desktop.py",
        SRC / "desktop_v2",
    )
    files = []
    for root in protected_roots:
        files.extend(root.rglob("*.py") if root.is_dir() else (root,))

    offenders = []
    for path in files:
        if not path.exists():
            continue
        targets = _import_targets(path)
        if any("research_evidence_sealing" in target for target in targets) or any(
            "Ed25519PrivateKey" in target for target in targets
        ):
            offenders.append(str(path.relative_to(SRC)))

    assert offenders == []


def test_b11_sealing_tool_does_not_import_the_runtime_composition():
    targets = _import_targets(SEALING)

    assert not any("composition" in target for target in targets)
    assert not any(
        term in target.lower()
        for target in targets
        for term in ("broker", "risk", "execution", "orderdispatch", "desktop")
    )


# -- framework and storage hygiene ---------------------------------------


def test_b12_domain_and_application_are_qt_free():
    for path in (DOMAIN, APPLICATION):
        targets = _import_targets(path)
        assert not any(
            "PySide" in target or "PyQt" in target or "desktop" in target.lower()
            for target in targets
        )


def test_b13_application_is_storage_free():
    targets = _import_targets(APPLICATION)

    assert not any(
        target == "sqlite3" or "adapters.sqlite" in target for target in targets
    )


def test_b14_application_has_no_broker_risk_or_execution_imports():
    targets = _import_targets(APPLICATION)

    assert not any(
        term in target.lower()
        for target in targets
        for term in ("broker", "risk", "execution", "portfolio")
    )


def test_b15_authentication_surface_has_no_lifecycle_calls():
    forbidden = {"transition", "clone_version", "update_deployment"}
    for path in B1_SURFACE:
        tree = ast.parse(_text(path))
        calls = {
            node.func.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        }
        assert not (calls & forbidden), path


def test_b16_authentication_does_not_become_promotion_authority():
    """B1 answers one question and must not start answering lifecycle's."""

    for path in B1_SURFACE:
        text = _text(path)
        assert "gate_passed" not in text
        assert "StrategyStatus" not in text
        assert "PortfolioOperatingPlan" not in text


def test_b17_authentication_stays_independent_of_the_strategy_gate():
    """B1 must not quietly absorb the gate's authority; coverage wires them."""

    for path in B1_SURFACE:
        assert "StrategyGateEvaluation" not in _text(path)


def test_b18_authentication_store_never_alters_the_frozen_schema():
    text = _text(REPOSITORY_ADAPTER)

    for frozen in ("strategy_version", "strategy_deployment"):
        assert f"CREATE TABLE {frozen}" not in text
        assert f"ALTER TABLE {frozen}" not in text
        assert f"DROP TABLE {frozen}" not in text


def test_b19_authentication_repository_is_separate_from_the_gate_repository():
    assert "StrategyGateRepository" not in _text(REPOSITORY_ADAPTER)
    assert "strategy_gate_evaluation" not in _text(REPOSITORY_ADAPTER)


def test_b20_no_ai_or_optimizer_imports_in_the_authentication_surface():
    for path in B1_SURFACE:
        targets = _import_targets(path)
        assert not any(
            term in target.lower()
            for target in targets
            for term in ("openai", "anthropic", "optimizer", "llm", "torch")
        )


def test_b21_no_hand_rolled_signature_verification():
    """Self-made crypto is exactly what this boundary must not do."""

    for path in B1_SURFACE:
        text = _text(path)
        assert "def verify(" not in text or path in {
            SIGNATURE_ADAPTER, VERIFICATION_PORT,
        }
        assert "pow(" not in text
        assert "modular" not in text.lower()


def test_b22_trust_store_holds_public_material_only():
    """Structurally: public keys and status, never a shared secret or key."""

    from us_quant.trading.domain.evidence_auth import (
        EvidenceKeyTrustStatus,
        EvidenceVerificationKey,
        trust_store_to_payload,
    )

    payload = trust_store_to_payload(
        (
            EvidenceVerificationKey(
                key_id="key-1",
                algorithm="ed25519",
                public_key=b"\x01" * 32,
                trust_status=EvidenceKeyTrustStatus.ACTIVE,
            ),
        )
    )

    assert set(payload) == {"schema_version", "keys"}
    assert set(payload["keys"][0]) == {
        "key_id", "algorithm", "public_key", "trust_status",
    }

    # And no shared-secret primitive is imported anywhere on the surface.
    for path in (DOMAIN, TRUST_STORE_ADAPTER, SIGNATURE_ADAPTER, APPLICATION):
        assert not any("hmac" in target.lower() for target in _import_targets(path))


def test_b23_application_reaches_the_artifact_only_through_its_port():
    """The loader is a port; only composition may name a concrete adapter.

    Without this the authenticator would import the artifact adapter directly,
    which is the one edge the inward-layer rules forbid -- and it would let
    whoever assembles the composition re-point the authentication decision at a
    different loader.
    """

    targets = _import_targets(APPLICATION)

    assert not any("trading.adapters" in target for target in targets)
    assert any("trading.ports.research_evidence_artifact" in target for target in targets)


def test_b24_composition_is_the_only_place_that_names_the_artifact_adapter():
    for path in (DOMAIN, APPLICATION, REPOSITORY_PORT, VERIFICATION_PORT, ARTIFACT_PORT):
        assert not any(
            "adapters.research_evidence" in target for target in _import_targets(path)
        )

    assert any(
        "adapters.research_evidence" in target for target in _import_targets(COMPOSITION)
    )
