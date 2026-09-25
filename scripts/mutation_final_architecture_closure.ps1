# Mutation verification for the Final Architecture Closure (FAC).
#
# Each entry breaks exactly one *cross-layer* safety boundary this round locked,
# then asserts that the guard or behaviour test naming that boundary actually
# fails.  A mutation that survives is a hole: either the guard is not testing
# what it claims, or the boundary is not locked.
#
# What this harness deliberately does NOT do is re-run the 165 mutants the
# earlier rounds already own (E2 13, E3 41, E4 11, F1 14, F2 22, G1 17, G2-A 12,
# G2-B 35).  Those are re-run separately, in full, as the historical gate; see
# ``scripts/mutation_final_architecture_closure.ps1``'s companion note in
# ``docs/TRADING_ARCHITECTURE_V2.md``.  Duplicating them here would double the
# wall time and tell us nothing new.
#
# The harness distinguishes three outcomes, and only `caught=True` counts as a
# caught mutation:
#
#   caught=True           the named tests ran and failed -- RED, as required;
#   caught=HARNESS-ERROR  the regex matched nothing, the mutation produced
#                         invalid Python, pytest exited 5 ("no tests collected"),
#                         or the output had no "N passed|failed" line.  Any of
#                         those is a hole in *this* script, and reporting one as
#                         "caught" would let a broken mutation look like a
#                         working guard;
#   caught=ERROR          an unexpected exception while mutating or restoring.
#
# A pattern that matches nothing is reported as HARNESS-ERROR and fails the
# script, so a dead anchor can never masquerade as a green gate.
#
# Run:
#
#     .\scripts\mutation_final_architecture_closure.ps1

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot

$env:PYTHONPATH = Join-Path $projectRoot "src"
$env:QT_QPA_PLATFORM = "offscreen"
$env:PYTHONUTF8 = "1"

function Resolve-Python {
    foreach ($candidate in @(
        $env:USQUANT_PYTHON,
        (Join-Path $projectRoot ".venv314\Scripts\python.exe"),
        (Join-Path $projectRoot ".venv313\Scripts\python.exe"),
        (Join-Path $projectRoot ".venv\Scripts\python.exe")
    )) {
        if ($candidate -and (Test-Path -LiteralPath $candidate)) {
            return $candidate
        }
    }
    $onPath = Get-Command python -ErrorAction SilentlyContinue
    if (-not $onPath) {
        Write-Host "No Python interpreter found (.venv314 / .venv313 / .venv / PATH)." -ForegroundColor Red
        exit 2
    }
    return $onPath.Source
}

$py = Resolve-Python

& $py -c "import pytest, PySide6" 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Host "The interpreter '$py' cannot import pytest / PySide6." -ForegroundColor Red
    exit 2
}

$src = Join-Path $projectRoot "src\us_quant"
$domainStrategy = Join-Path $src "trading\domain\strategy.py"
$domainCommon = Join-Path $src "trading\domain\common.py"
$appRisk = Join-Path $src "trading\application\risk.py"
$appExecution = Join-Path $src "trading\application\execution.py"
$appStrategies = Join-Path $src "trading\application\strategies.py"
$rtDispatch = Join-Path $src "trading\runtime\dispatch.py"
$rtTrading = Join-Path $src "trading\runtime\trading.py"
$rtWorkflowState = Join-Path $src "trading\runtime\workflow_state.py"
$desktopWorkflows = Join-Path $src "desktop_v2\workflows.py"
$compositionExecution = Join-Path $src "trading\composition\execution.py"

$fac = Join-Path $projectRoot "tests/test_final_architecture_closure.py"
$execBehaviour = Join-Path $projectRoot "tests/test_trading_execution_application.py"
$workflowState = Join-Path $projectRoot "tests/test_runtime_workflow_state.py"
$strategyApp = Join-Path $projectRoot "tests/test_trading_strategy_application.py"

function Get-Text([string]$path) { [System.IO.File]::ReadAllText($path) }
function Set-Text([string]$path, [string]$text) {
    [System.IO.File]::WriteAllText(
        $path, $text, (New-Object System.Text.UTF8Encoding($false))
    )
}

# Every ``find`` below is runtime-verified to match **exactly one** location in
# its target (case-insensitively).  The harness does that check itself rather
# than relying on the author to keep the patterns unique: 0 matches and 2+
# matches are both HARNESS-ERROR.  This matters because the static
# ``[regex]::Replace(input, pattern, replacement, 1)`` overload binds that ``1``
# to ``RegexOptions`` (1 == IgnoreCase), **not** to a replacement count -- so a
# duplicated pattern would silently rewrite several sites at once and turn a
# single-point mutant into a multi-point one that still reported as caught.
# The instance overload ``$regex.Replace(input, replacement, 1)`` used below
# takes a real count, and the preceding match check makes it exact.
$mutations = @(
    # -- import / layer boundaries -------------------------------------
    @{
        name = 'M1  the domain imports the desktop'
        file = $domainStrategy
        find = 'from us_quant\.trading\.domain\.common import ZERO, freeze_parameters'
        repl = "from us_quant.trading.domain.common import ZERO, freeze_parameters`nimport us_quant.desktop_v2.navigation"
        tests = @($fac)
        select = @("-k", "layers_reach_nothing_they_must_not")
    },
    @{
        name = 'M1b the domain imports a desktop-adjacent root module'
        file = $domainStrategy
        find = 'from us_quant\.trading\.domain\.common import ZERO, freeze_parameters'
        repl = "from us_quant.trading.domain.common import ZERO, freeze_parameters`nimport us_quant.desktop_workers"
        tests = @($fac)
        select = @("-k", "layers_reach_nothing_they_must_not")
    },
    @{
        name = 'M2  the domain imports Qt'
        file = $domainStrategy
        find = 'from us_quant\.trading\.domain\.common import ZERO, freeze_parameters'
        repl = "from us_quant.trading.domain.common import ZERO, freeze_parameters`nfrom PySide6.QtCore import QObject"
        tests = @($fac)
        select = @("-k", "layers_reach_nothing_they_must_not")
    },
    @{
        name = 'M3  the risk application imports the concrete IBKR adapter'
        file = $appRisk
        find = 'class RiskApplication:'
        repl = "from us_quant.trading.adapters.ibkr.execution import IBKRExecutionAdapter`n`n`nclass RiskApplication:"
        tests = @($fac)
        select = @("-k", "layers_reach_nothing_they_must_not or applications_name_no_concrete_broker")
    },
    @{
        name = 'M4  the execution application imports the concrete IBKR adapter'
        file = $appExecution
        find = 'class ExecutionApplication:'
        repl = "from us_quant.trading.adapters.ibkr.execution import IBKRExecutionAdapter`n`n`nclass ExecutionApplication:"
        tests = @($fac)
        select = @("-k", "layers_reach_nothing_they_must_not or applications_name_no_concrete_broker")
    },
    @{
        name = 'M5  the runtime imports the concrete SQLite order store'
        file = $rtDispatch
        find = 'from us_quant\.trading\.domain\.orders import OrderIntent'
        repl = "from us_quant.trading.domain.orders import OrderIntent`nfrom us_quant.trading.adapters.sqlite.order_repository import SQLiteOrderRepository"
        tests = @($fac)
        select = @("-k", "layers_reach_nothing_they_must_not")
    },
    @{
        name = 'M6  the execution orchestrator imports a sibling capability'
        file = (Join-Path $src "desktop_v2\orchestration\execution\queries.py")
        find = 'from us_quant\.trading\.domain\.strategy import StrategyStatus, StrategyVersion'
        repl = "from us_quant.trading.domain.strategy import StrategyStatus, StrategyVersion`nfrom us_quant.desktop_v2.orchestration.paper import queries as _paper_queries"
        tests = @($fac)
        select = @("-k", "capability_orchestrators_do_not_import_each_other")
    },

    @{
        name = 'M6b the account application reaches a second IBKR symbol'
        file = (Join-Path $src "trading\application\accounts.py")
        find = 'from us_quant\.ibkr import IBKRConnectionConfig'
        repl = "from us_quant.ibkr import IBKRConnectionConfig`nfrom us_quant.ibkr import connect_ibkr_client"
        tests = @($fac)
        select = @("-k", "application_layer_exceptions_are_symbol_scoped")
    },
    @{
        name = 'M6c the Paper application reaches the lease manager'
        file = (Join-Path $src "trading\application\paper\service.py")
        find = 'from us_quant\.trading\.runtime\.workflow_state import PaperWorkflowPhase'
        repl = "from us_quant.trading.runtime.workflow_state import PaperWorkflowPhase`nfrom us_quant.trading.runtime.workflow_state import ExecutionLeaseManager"
        tests = @($fac)
        select = @("-k", "application_layer_exceptions_are_symbol_scoped")
    },
    @{
        name = 'M6d the application imports the whole provider module'
        file = (Join-Path $src "trading\application\accounts.py")
        find = 'from us_quant\.ibkr import IBKRConnectionConfig'
        repl = "import us_quant.ibkr`nfrom us_quant.ibkr import IBKRConnectionConfig"
        tests = @($fac)
        select = @("-k", "takes_a_symbol_scoped_module_whole or application_layer_exceptions_are_symbol_scoped")
    },
    # The three below are the relative-spelling half.  An earlier FA4c compared
    # the raw ``node.module`` against its ``us_quant.ibkr`` key, so a relative
    # import resolved to the right package by the layer guards but compared as
    # ``"ibkr"`` here -- and the mutant appeared caught only because it tripped
    # the coverage bookkeeping, which is a false pass rather than a detection.
    # Both guards now resolve through one shared helper.
    @{
        name = 'M6e the account application uses a relative forbidden IBKR symbol'
        file = (Join-Path $src "trading\application\accounts.py")
        find = 'from us_quant\.ibkr import IBKRConnectionConfig'
        repl = "from ...ibkr import (`n    IBKRConnectionConfig,`n    connect_ibkr_client,`n)"
        tests = @($fac)
        select = @("-k", "application_layer_exceptions_are_symbol_scoped")
    },
    @{
        name = 'M6f the Paper application uses a relative forbidden workflow symbol'
        file = (Join-Path $src "trading\application\paper\service.py")
        find = 'from us_quant\.trading\.runtime\.workflow_state import PaperWorkflowPhase'
        repl = "from ...runtime.workflow_state import (`n    PaperWorkflowPhase,`n    ExecutionLeaseManager,`n)"
        tests = @($fac)
        select = @("-k", "application_layer_exceptions_are_symbol_scoped")
    },
    @{
        name = 'M6g the application uses a relative star import of the provider'
        file = (Join-Path $src "trading\application\accounts.py")
        find = 'from us_quant\.ibkr import IBKRConnectionConfig'
        repl = "from ...ibkr import *`nfrom us_quant.ibkr import IBKRConnectionConfig"
        tests = @($fac)
        select = @("-k", "takes_a_symbol_scoped_module_whole")
    },
    @{
        name = 'M6h the Paper application imports the whole workflow-state module'
        # The hole the whole-module guard was extended to close: taking the
        # module whole makes ExecutionLeaseManager / validate_paper_transition /
        # ExecutionLease / WorkflowStateError reachable by attribute access, so
        # the PaperWorkflowPhase-only seam becomes a whole door again.  The
        # rebind keeps ``PaperWorkflowPhase`` defined, so the mutant cannot be
        # killed by a NameError -- only by the FA4d assertion.
        file = (Join-Path $src "trading\application\paper\service.py")
        find = 'from us_quant\.trading\.runtime\.workflow_state import PaperWorkflowPhase'
        repl = "import us_quant.trading.runtime.workflow_state as _workflow_state`nPaperWorkflowPhase = _workflow_state.PaperWorkflowPhase"
        tests = @($fac)
        select = @("-k", "takes_a_symbol_scoped_module_whole")
    },

    # -- Risk -> Execution path ----------------------------------------
    @{
        name = 'M7  TradeProposal gains an order identity field'
        file = $domainStrategy
        find = '    strategy: StrategyIdentity\r?\n    symbol: str\r?\n    action: TradeAction'
        repl = "    strategy: StrategyIdentity`n    order_id: str`n    symbol: str`n    action: TradeAction"
        tests = @($fac)
        select = @("-k", "trade_proposal_carries_nothing_submittable")
    },
    @{
        name = 'M8  the session calls submit_approved directly, bypassing the dispatch'
        file = $rtTrading
        find = '        outcome, blockages = self\.dispatch\.run_entries\('
        repl = "        self.execution.submit_approved(proposal=None, decision=None, execution_symbol='', session_id='', reason='')`n        outcome, blockages = self.dispatch.run_entries("
        tests = @($fac)
        select = @("-k", "dispatch_is_the_only_runtime_risk_execution_crossing_point")
    },
    @{
        name = 'M9  the dispatch submits without checking the verdict'
        file = $rtDispatch
        find = '        if not decision\.approved:\r?\n            return DispatchOutcome\(\r?\n                blockage=\('
        repl = "        if False:`n            return DispatchOutcome(`n                blockage=("
        tests = @($fac)
        select = @("-k", "dispatch_never_submits_an_unapproved_verdict")
    },
    @{
        name = 'M10 the execution application drops its approved check'
        file = $appExecution
        find = '        if not decision\.approved:\r?\n            raise ExecutionRefused\(\r?\n                "[^"]*"\r?\n            \)'
        repl = "        if False:`n            raise ExecutionRefused(`n                `"fac`"`n            )"
        tests = @($fac)
        select = @("-k", "execution_application_fails_closed_on_a_bad_verdict")
    },
    @{
        name = 'M11 the execution application drops its quantity-ceiling check'
        file = $appExecution
        find = '        if decision\.approved_quantity > proposal\.desired_quantity:\r?\n            raise ExecutionRefused\(\r?\n                "[^"]*"\r?\n            \)'
        repl = "        if False:`n            raise ExecutionRefused(`n                `"fac`"`n            )"
        tests = @($fac)
        select = @("-k", "execution_application_fails_closed_on_a_bad_verdict")
    },
    @{
        name = 'M12 the execution application drops its same-request check'
        file = $appExecution
        find = '        if decision\.requested_quantity != proposal\.desired_quantity:\r?\n            raise ExecutionRefused\(\r?\n                "[^"]*"\r?\n            \)'
        repl = "        if False:`n            raise ExecutionRefused(`n                `"fac`"`n            )"
        tests = @($fac)
        select = @("-k", "execution_application_fails_closed_on_a_bad_verdict")
    },
    @{
        name = 'M13 the durable write is moved after the broker submit'
        file = $appExecution
        find = '        reservation = self\._broker\.reserve\(intent\)\r?\n        self\._repository\.record_intent\(\r?\n            intent,\r?\n            broker_order_id=reservation\.broker_order_id,\r?\n            account_alias=reservation\.account_alias,\r?\n        \)'
        repl = "        reservation = self._broker.reserve(intent)"
        tests = @($fac)
        select = @("-k", "durable_write_precedes_the_broker_submit")
    },

    # -- Paper / Shadow exclusivity ------------------------------------
    @{
        name = 'M14 Paper and Shadow get two independent lease managers'
        file = $desktopWorkflows
        find = '        self\.shadow = ShadowWorkflowController\(leases=leases\)\r?\n        self\.paper = PaperWorkflowController\(leases=leases\)'
        repl = "        self.shadow = ShadowWorkflowController(leases=ExecutionLeaseManager())`n        self.paper = PaperWorkflowController(leases=ExecutionLeaseManager())"
        tests = @($fac)
        select = @("-k", "paper_and_shadow_share_one_lease_manager or the_two_workflows_exclude_each_other")
    },
    @{
        name = 'M15 the shared lease is acquired for SHADOW while PAPER is held'
        file = $rtWorkflowState
        find = '    def acquire_shadow\(self\) -> None:\r?\n        if self\._lease is not ExecutionLease\.NONE:'
        repl = "    def acquire_shadow(self) -> None:`n        if False:"
        tests = @($fac)
        select = @("-k", "the_two_workflows_exclude_each_other")
    },
    @{
        name = 'M16 the PAPER lease can be released before finalization'
        file = $rtWorkflowState
        find = '        if not finalized:\r?\n            raise WorkflowStateError\("[^"]*"\)'
        repl = "        if False:`n            raise WorkflowStateError(`"fac`")"
        tests = @($fac)
        select = @("-k", "paper_lease_cannot_be_released_before_finalization")
    },

    # -- Paper lifecycle / recovery ------------------------------------
    @{
        name = 'M17 HALTED gains an automatic route back to RUNNING'
        file = $rtWorkflowState
        find = '    PaperWorkflowPhase\.HALTED: frozenset\(\),'
        repl = "    PaperWorkflowPhase.HALTED: frozenset({PaperWorkflowPhase.RUNNING}),"
        tests = @($fac)
        select = @("-k", "halted_session_has_no_automatic_route_back_to_running")
    },
    @{
        name = 'M18 the explicit-reconciliation requirement is dropped'
        file = $rtWorkflowState
        find = '    if explicit_reconciliation and \('
        repl = "    if False and ("
        tests = @($fac)
        select = @("-k", "halted_session_has_no_automatic_route_back_to_running or manual_reconciliation_is_an_explicit_two_step_route")
    },
    @{
        name = 'M19 the window reads the Paper workflow phase again'
        file = (Join-Path $src "desktop.py")
        find = '        self\.runtime_supervisor = RuntimeSupervisor\(\)'
        repl = "        from us_quant.trading.runtime.workflow_state import PaperWorkflowPhase`n        self.runtime_supervisor = RuntimeSupervisor()"
        tests = @($fac)
        select = @("-k", "only_the_paper_capability_reads_the_workflow_phase or the_window_still_holds_no_capability_truth")
    },

    # -- Broker abstraction --------------------------------------------
    @{
        name = 'M20 the execution builder takes a concrete adapter instead of the port'
        file = $compositionExecution
        find = '    broker: BrokerExecutionPort,\r?\n\) -> ExecutionApplication:'
        repl = "    broker: IBKRExecutionAdapter,`n) -> ExecutionApplication:"
        tests = @($fac)
        select = @("-k", "execution_builder_accepts_the_port_abstraction")
    },
    @{
        name = 'M20b a parallel Live risk authority is introduced'
        file = $appRisk
        find = 'class RiskApplication:'
        repl = "class LiveRiskApplication:`n    pass`n`n`nclass RiskApplication:"
        tests = @($fac)
        select = @("-k", "future_live_path_must_reuse_the_single_authority_stack")
    },
    @{
        name = 'M20c a parallel Live execution authority is introduced'
        file = $appExecution
        find = 'class ExecutionApplication:'
        repl = "class LiveExecutionApplication:`n    pass`n`n`nclass ExecutionApplication:"
        tests = @($fac)
        select = @("-k", "future_live_path_must_reuse_the_single_authority_stack")
    },
    @{
        name = 'M20d the execution authority branches on a paper/live mode'
        file = $appExecution
        find = '        self\._repository = repository\r?\n        self\._broker = broker'
        repl = "        self._repository = repository`n        self._broker = broker`n        self._mode = `"paper`""
        tests = @($fac)
        select = @("-k", "paper_and_live_differences_are_not_a_mode_branch")
    },
    # -- Strategy immutable lifecycle ----------------------------------
    @{
        name = 'M22 a governed version stores a plain mutable parameter dict again'
        file = $domainStrategy
        find = '        object\.__setattr__\(\r?\n            self, "parameters", freeze_parameters\(dict\(self\.parameters\)\)\r?\n        \)'
        repl = "        object.__setattr__(self, `"parameters`", dict(self.parameters))"
        tests = @($fac)
        select = @("-k", "governed_versions_parameters_cannot_be_edited_in_place or governed_versions_parameters_survive_the_copy_protocol")
    },
    @{
        name = 'M23 the frozen parameter mapping allows in-place writes'
        file = $domainCommon
        find = '    __setitem__ = _refuse\r?\n    __delitem__ = _refuse\r?\n    __ior__ = _refuse\r?\n    clear = _refuse\r?\n    pop = _refuse\r?\n    popitem = _refuse\r?\n    setdefault = _refuse\r?\n    update = _refuse\r?\n\r?\n    def __reduce__\(self\) -> tuple\[Any, \.\.\.\]:\r?\n        return \(FrozenParameters, \(dict\(self\),\)\)'
        repl = "    def __reduce__(self) -> tuple[Any, ...]:`n        return (FrozenParameters, (dict(self),))"
        tests = @($fac)
        select = @("-k", "governed_versions_parameters_cannot_be_edited_in_place")
    },
    @{
        name = 'M24 nested parameter lists are left mutable'
        file = $domainCommon
        find = '    if isinstance\(value, list\):\r?\n        return FrozenList\(freeze_parameters\(item\) for item in value\)'
        repl = "    if isinstance(value, list):`n        return value"
        tests = @($fac)
        select = @("-k", "governed_versions_parameters_cannot_be_edited_in_place")
    },
    @{
        name = 'M24b a frozen mapping can be re-populated through __init__'
        file = $domainCommon
        # Anchored on ``dict.__init__`` in ``__new__`` so it matches the
        # FrozenParameters seal only -- the FrozenList seal is byte-identical
        # apart from that line, and a pattern matching both would mutate two
        # classes at once.
        find = '        dict\.__init__\(instance, \*args, \*\*kwargs\)\r?\n        return instance\r?\n\r?\n    def __init__\(self, \*args: Any, \*\*kwargs: Any\) -> None:\r?\n        if getattr\(self, "_sealed", False\):\r?\n            raise TypeError\(\r?\n                "[^"]*"\r?\n            \)\r?\n        object\.__setattr__\(self, "_sealed", True\)'
        repl = "        dict.__init__(instance, *args, **kwargs)`n        return instance`n`n    def __init__(self, *args: Any, **kwargs: Any) -> None:`n        object.__setattr__(self, `"_sealed`", True)"
        tests = @($fac)
        select = @("-k", "frozen_mapping_cannot_be_re_populated_through_init or governed_versions_parameters_cannot_be_edited_in_place")
    },
    @{
        name = 'M24c tuple descendants are left mutable'
        file = $domainCommon
        # A tuple cannot be edited in place, but the dicts and lists inside it
        # can -- and json.dumps accepts a tuple, so it is a legitimate parameter
        # value.  Dropping this branch is what re-opens the split identity.
        find = '    if isinstance\(value, tuple\):\r?\n        return tuple\(freeze_parameters\(item\) for item in value\)\r?\n'
        repl = ""
        tests = @($fac)
        select = @("-k", "tuple_descendants_of_governed_parameters_are_frozen")
    },
    @{
        name = 'M24d the copy protocol loses the frozen representation'
        # Targets ``__copy__`` -- i.e. the ``copy.copy(...)`` protocol, which
        # FA26b asserts preserves the frozen form.  NOT the inherited
        # ``FrozenParameters.copy()``, which is documented and asserted to return
        # a plain detached dict; a label saying "the inherited copy()" would
        # contradict that and make the audit ambiguous.
        file = $domainCommon
        find = '    def __copy__\(self\) -> "FrozenParameters":\r?\n        return FrozenParameters\(dict\(self\)\)'
        repl = "    def __copy__(self) -> `"FrozenParameters`":`n        return dict(self)"
        tests = @($fac)
        select = @("-k", "governed_versions_parameters_survive_the_copy_protocol")
    },
    @{
        name = 'M25 register accepts a callers own gate attestation'
        file = $appStrategies
        find = '        if gate_passed:\r?\n            raise StrategyApplicationError\(\r?\n                "[^"]*"\r?\n            \)'
        repl = "        if False:`n            raise StrategyApplicationError(`"fac`")"
        tests = @($fac)
        select = @("-k", "register_refuses_a_callers_own_gate_attestation")
    },
    @{
        name = 'M26 a clone inherits the source version gate state'
        file = $appStrategies
        find = '            gate_passed=False,\r?\n            gate_reason="[^"]*",'
        repl = "            gate_passed=source.gate_passed,`n            gate_reason=source.gate_reason,"
        tests = @($fac)
        select = @("-k", "cloning_a_version_resets_its_evidence_state")
    },
    @{
        name = 'M27 entering PAPER_SHADOW no longer requires the gate'
        file = $appStrategies
        find = '        if \(\r?\n            target is StrategyStatus\.PAPER_SHADOW\r?\n            and not current\.gate_passed\r?\n        \):\r?\n            raise StrategyApplicationError\(\r?\n                f"research gate blocked: \{current\.gate_reason\}"\r?\n            \)'
        repl = "        if False:`n            raise StrategyApplicationError(`"fac`")"
        tests = @($fac)
        select = @("-k", "entering_paper_shadow_requires_the_gate")
    },
    @{
        name = 'M28 STOPPED gains a transition back to PAPER_SHADOW'
        file = $domainStrategy
        find = '    StrategyStatus\.STOPPED: frozenset\(\),'
        repl = "    StrategyStatus.STOPPED: frozenset({StrategyStatus.PAPER_SHADOW}),"
        tests = @($fac)
        select = @("-k", "stopped_and_legacy_invalidated_are_terminal")
    },
    @{
        name = 'M29 a retired version may be cloned'
        file = $appStrategies
        find = '        if source\.status is StrategyStatus\.LEGACY_INVALIDATED:\r?\n            raise StrategyApplicationError\(\r?\n                "[^"]*"\r?\n            \)'
        repl = "        if False:`n            raise StrategyApplicationError(`"fac`")"
        tests = @($fac)
        select = @("-k", "retired_version_cannot_be_cloned")
    }
)

Write-Host "interpreter: $py" -ForegroundColor Cyan

$results = @()
foreach ($mutation in $mutations) {
    $path = $mutation.file
    $original = Get-Text $path
    $backup = "$path.mutation-backup"
    Copy-Item -LiteralPath $path -Destination $backup -Force
    $caught = $null
    $detail = ""
    # Reset per iteration: a stale value would let a later mutant skip
    # its own replacement-equality check.
    $mutated = $null
    try {
        $regex = [regex]::new(
            $mutation.find,
            [System.Text.RegularExpressions.RegexOptions]::IgnoreCase
        )
        # ``$patternMatches``, not ``$matches``: the latter is a PowerShell
        # automatic variable set by ``-match``.
        $patternMatches = $regex.Matches($original)
        if ($patternMatches.Count -ne 1) {
            $caught = "HARNESS-ERROR"
            $detail = "the mutation pattern matched $($patternMatches.Count) locations; expected exactly 1"
        }
        else {
            $mutated = $regex.Replace($original, $mutation.repl, 1)
        }
        if ($null -eq $caught -and $mutated -eq $original) {
            $caught = "HARNESS-ERROR"
            $detail = "the mutation applied nothing (the replacement reproduced the original text)"
        }
        if ($null -eq $caught) {
            Set-Text $path $mutated
            $syntax = & $py -c "import ast, sys; ast.parse(open(sys.argv[1], encoding='utf-8').read())" $path 2>&1
            if ($LASTEXITCODE -ne 0) {
                $caught = "HARNESS-ERROR"
                $detail = "the mutation produced invalid syntax: $($syntax | Select-Object -Last 1)"
            }
            else {
                $arguments = @("-m", "pytest", "-q", "-p", "no:cacheprovider") + $mutation.tests + $mutation.select
                $output = & $py @arguments 2>&1
                $exit = $LASTEXITCODE
                # Only exit 1 with a failure count is RED.  Exit 0 is a
                # survivor; 5 is "no tests collected"; anything else (2
                # collection/interrupt, 3 internal, 4 usage) means the suite
                # never ran, which is a hole in *this script* and not evidence
                # that the guard fired.
                $countLine = $output | Select-String -Pattern '\d+ (passed|failed)' | Select-Object -Last 1
                if ($exit -eq 5) {
                    $caught = "HARNESS-ERROR"
                    $detail = "no tests were selected by $($mutation.select -join ' ')"
                }
                elseif ($exit -notin 0, 1) {
                    $caught = "HARNESS-ERROR"
                    $detail = "pytest exited $exit (collection/usage/internal error), not an assertion failure"
                }
                elseif (-not $countLine) {
                    $caught = "HARNESS-ERROR"
                    $detail = "no test count in the output"
                }
                else {
                    $caught = ($exit -ne 0)
                    $detail = $countLine
                }
            }
        }
    }
    catch {
        $caught = "ERROR"
        $detail = $_.Exception.Message
    }
    finally {
        Copy-Item -LiteralPath $backup -Destination $path -Force
        Remove-Item -LiteralPath $backup -Force
    }
    $results += [pscustomobject]@{
        Mutation = $mutation.name
        Caught = $caught
        Detail = "$detail"
    }
    Write-Host ("{0,-62} caught={1}  {2}" -f $mutation.name, $caught, $detail)
}

Write-Host ""
Write-Host "=== summary ==="
$results | Format-Table -AutoSize | Out-String | Write-Host
$uncaught = @($results | Where-Object { $_.Caught -ne $true })
Write-Host "mutations: $($results.Count)   red: $(@($results | Where-Object { $_.Caught -eq $true }).Count)   not caught: $($uncaught.Count)"
foreach ($row in $uncaught) { Write-Host "  SURVIVED: $($row.Mutation) -> $($row.Detail)" }

if ($uncaught.Count -gt 0) { exit 1 }
exit 0
