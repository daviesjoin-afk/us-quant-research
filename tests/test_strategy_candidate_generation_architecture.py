import ast
import inspect
from pathlib import Path

from us_quant.trading.application import strategy_candidate_generation as candidate_app
from us_quant.trading.application.strategies import StrategyApplication
from us_quant.trading.domain import strategy_search


ROOT = Path(__file__).resolve().parents[1]


def _tree(path):
    return ast.parse(path.read_text(encoding="utf-8"))


def _imports(tree):
    result = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            result.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            result.add(node.module)
    return result


def test_candidate_application_has_only_search_and_strategy_application_edges():
    imports = _imports(_tree(Path(inspect.getsourcefile(candidate_app))))
    forbidden = (
        "sqlite", "strategy_paper_performance", "evidence_authentication",
        "strategy_gate", "strategy_coverage", "strategy_lifecycle",
        "broker", "risk", "execution", "dispatch", "ai",
    )
    assert not any(
        token in name.lower().split(".")
        for name in imports
        for token in forbidden
    )
    assert "us_quant.trading.application.strategies" in imports
    assert "us_quant.trading.ports.strategy_search_repository" in imports


def test_search_domain_is_pure_and_has_one_existing_parameter_authority():
    tree = _tree(Path(inspect.getsourcefile(strategy_search)))
    imports = _imports(tree)
    forbidden = ("sqlite", "application", "clock", "random", "broker", "ai")
    assert not any(
        token in name.lower().split(".")
        for name in imports
        for token in forbidden
    )
    calls = [
        node.func.id for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    ]
    assert "validate_strategy_parameters" in calls
    source = Path(inspect.getsourcefile(strategy_search)).read_text(encoding="utf-8")
    assert source.count("validate_strategy_parameters(") == 1


def test_strategy_application_has_one_strategy_version_constructor_and_writer():
    tree = _tree(Path(inspect.getsourcefile(StrategyApplication)))
    constructors = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "StrategyVersion"
    ]
    assert len(constructors) == 1
    inserts = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "insert_version"
    ]
    assert len(inserts) == 1


def test_candidate_clone_cannot_accept_governance_or_gate_overrides():
    signature = inspect.signature(StrategyApplication.clone_research_candidate)
    assert set(signature.parameters) == {
        "self", "parent_version_id", "candidate_version_id", "semver", "parameters"
    }
    body = inspect.getsource(StrategyApplication.clone_research_candidate)
    assert "status=StrategyStatus.RESEARCH" in body
    assert "mode=StrategyMode.RESEARCH" in body
    assert "gate_passed" not in signature.parameters
    assert "gate_passed=False" in inspect.getsource(StrategyApplication._create_version)


def test_candidate_identity_helpers_have_no_clock_uuid_or_ordinal_inputs():
    candidate_signature = inspect.signature(strategy_search.candidate_version_id_for)
    generation_signature = inspect.signature(strategy_search.generation_id_for)
    assert "ordinal" not in candidate_signature.parameters
    assert "generated_at" not in generation_signature.parameters
    source = Path(inspect.getsourcefile(strategy_search)).read_text(encoding="utf-8")
    assert "uuid" not in source.lower()
    assert "datetime.now" not in source
    assert "random" not in source.lower()
    assert "ordinal" not in inspect.getsource(strategy_search.candidate_version_id_for)
    assert "generated_at" not in inspect.getsource(strategy_search.generation_id_for)


def test_composition_contains_only_the_three_candidate_path_components():
    path = ROOT / "src/us_quant/trading/composition/strategy_candidate_generation.py"
    imports = _imports(_tree(path))
    assert imports == {
        "__future__", "pathlib",
        "us_quant.trading.adapters.sqlite.strategy_repository",
        "us_quant.trading.adapters.sqlite.strategy_search_repository",
        "us_quant.trading.application.strategies",
        "us_quant.trading.application.strategy_candidate_generation",
    }


def test_existing_manual_clone_contract_remains_separate():
    source = inspect.getsource(StrategyApplication.clone_version)
    assert "self.register(" in source
    assert "uuid4" not in source
    tests = (ROOT / "tests/test_trading_strategy_application.py").read_text(
        encoding="utf-8"
    )
    assert "def test_a_clone_is_a_new_version" in tests
    assert "def test_a_clone_restarts_governance" in tests
