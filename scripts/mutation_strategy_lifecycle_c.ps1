# Stage 6-C governed lifecycle authority mutation harness.
#
# Each mutation breaks one security-relevant branch and asserts that a specific
# test turns RED.  A survivor means the suite does not pin the property.
#
# Run with -ValidateAnchors to check every regex anchors exactly once.

param([switch]$ValidateAnchors)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$env:PYTHONPATH = Join-Path $projectRoot "src"
$env:PYTHONUTF8 = "1"
$env:QT_QPA_PLATFORM = "offscreen"
$py = $env:US_QUANT_PYTHON
if (-not $py) { $py = Join-Path $projectRoot ".venv\Scripts\python.exe" }
if (-not (Test-Path -LiteralPath $py)) { $py = (Get-Command python -ErrorAction Stop).Source }

$domain = Join-Path $projectRoot "src\us_quant\trading\domain\strategy_lifecycle.py"
$application = Join-Path $projectRoot "src\us_quant\trading\application\strategy_lifecycle.py"
$strategies = Join-Path $projectRoot "src\us_quant\trading\application\strategies.py"
$repository = Join-Path $projectRoot "src\us_quant\trading\adapters\sqlite\strategy_lifecycle_repository.py"
$runtimeConfig = Join-Path $projectRoot "src\us_quant\trading\runtime\config.py"
$launchGate = Join-Path $projectRoot "src\us_quant\trading\application\paper_authorization.py"
$planBoundary = Join-Path $projectRoot "src\us_quant\trading\application\portfolio_operations.py"
$appPaths = Join-Path $projectRoot "src\us_quant\paths.py"

$tEvaluator = Join-Path $projectRoot "tests\test_strategy_lifecycle_evaluator.py"
$tService = Join-Path $projectRoot "tests\test_strategy_lifecycle_service.py"
$tRepository = Join-Path $projectRoot "tests\test_strategy_lifecycle_repository.py"
$tArchitecture = Join-Path $projectRoot "tests\test_strategy_lifecycle_architecture.py"
$tStrategies = Join-Path $projectRoot "tests\test_trading_strategy_application.py"
$tCoverageValidity = Join-Path $projectRoot "tests/test_strategy_coverage_validity.py"
$tCoverageValidityArchitecture = Join-Path $projectRoot "tests/test_strategy_coverage_validity.py"
$coverageValidity = Join-Path $projectRoot "src\us_quant\trading\application\strategy_coverage_validity.py"
$tLaunchGate = Join-Path $projectRoot "tests\test_paper_authorization.py"
$tPortfolioOps = Join-Path $projectRoot "tests\test_portfolio_operations.py"

$mutations = @(
    # -- authorization is the only bridge ---------------------------------
    @{ name='M01 authorization token guard removed'; file=$domain; find='if _controller_token is not _CONTROLLER_TOKEN:'; repl='if False:'; tests=@($tEvaluator); select='test_authorization_cannot_be_minted_without_the_controller_token' },
    @{ name='M02 paper targets no longer need an authorization'; file=$strategies; find='if target in AUTHORIZATION_REQUIRED_TARGETS:'; repl='if False:'; tests=@($tStrategies); select='test_paper_targets_require_a_lifecycle_authorization' },
    @{ name='M03 authorization for another version accepted'; file=$strategies; find='if authorization\.version_id != version_id:'; repl='if False:'; tests=@($tStrategies); select='test_an_authorization_for_another_version_or_target_is_refused' },
    @{ name='M04 authorization for another target accepted'; file=$strategies; find='if authorization\.target_status is not target:'; repl='if False:'; tests=@($tStrategies); select='test_an_authorization_for_another_version_or_target_is_refused' },
    @{ name='M05 gate_passed becomes promotion authority again'; file=$strategies; find='if target in AUTHORIZATION_REQUIRED_TARGETS:'; repl="if target is StrategyStatus.PAPER_SHADOW and not current.gate_passed:`n            raise StrategyApplicationError(`"legacy gate blocked`")`n        if target in AUTHORIZATION_REQUIRED_TARGETS:"; tests=@($tArchitecture); select='test_l04_the_transition_path_never_consults_gate_passed' },
    @{ name='M06 STOPPED becomes an autonomous action'; file=$strategies; find='AUTHORIZATION_REQUIRED_TARGETS = frozenset\(\s*\n\s*\{StrategyStatus\.PAPER_SHADOW, StrategyStatus\.PAUSED\}\s*\n\s*\)'; repl="AUTHORIZATION_REQUIRED_TARGETS = frozenset(`n    {StrategyStatus.PAPER_SHADOW, StrategyStatus.PAUSED, StrategyStatus.STOPPED}`n)"; tests=@($tArchitecture); select='test_l05_the_evidence_driven_targets_are_exactly_the_paper_ones' },
    @{ name='M07 STOPPED added as a lifecycle action'; file=$domain; find='    PROMOTE_TO_PAPER_SHADOW = "PROMOTE_TO_PAPER_SHADOW"'; repl="    PROMOTE_TO_PAPER_SHADOW = `"PROMOTE_TO_PAPER_SHADOW`"`n    STOPPED = `"STOPPED`""; tests=@($tArchitecture); select='test_l06_stopped_is_not_a_lifecycle_action' },

    # -- policy authority --------------------------------------------------
    @{ name='M08 a missing policy no longer fails closed'; file=$application; find='if not isinstance\(policy, StrategyLifecyclePolicy\):'; repl='if False:'; tests=@($tEvaluator); select='test_no_policy_authorises_nothing' },
    @{ name='M09 unsupported policy version accepted'; file=$application; find='if policy\.policy_version not in SUPPORTED_LIFECYCLE_POLICY_VERSIONS:'; repl='if False:'; tests=@($tEvaluator); select='test_unsupported_policy_version_fails_closed' },
    @{ name='M10 an action the policy forbids is allowed'; file=$application; find='if not policy\.permits\(action\):'; repl='if False:'; tests=@($tEvaluator); select='test_an_action_the_policy_does_not_permit_is_refused' },
    @{ name='M11 the source status is not checked'; file=$application; find='if version\.status is not ACTION_SOURCE_STATUS\[action\]:'; repl='if False:'; tests=@($tEvaluator); select='test_the_current_status_must_match_the_action' },

    # -- the evidence chain ------------------------------------------------
    @{ name='M12 a failed gate no longer blocks promotion'; file=$coverageValidity; find='if gate\.verdict is not StrategyGateVerdict\.PASS:'; repl='if False:'; tests=@($tCoverageValidity); select='test_a_gate_that_no_longer_passes_invalidates_the_claim' },
    @{ name='M13 a failed coverage no longer blocks promotion'; file=$application; find='if coverage\.verdict is not StrategyCoverageVerdict\.PASS:'; repl='if False:'; tests=@($tEvaluator); select='test_a_failed_coverage_blocks_promotion' },
    @{ name='M14 the required gate policy revision is not checked'; file=$coverageValidity; find='if gate\.policy_version != required_gate_policy_version:'; repl='if False:'; tests=@($tCoverageValidity); select='test_a_gate_from_another_policy_revision_invalidates_the_claim' },
    @{ name='M15 the required coverage policy revision is not checked'; file=$application; find='if coverage\.policy_version != policy\.required_coverage_policy_version:'; repl='if False:'; tests=@($tEvaluator); select='test_coverage_policy_revision_mismatch_blocks' },
    @{ name='M16 a revoked key still authorises promotion'; file=$coverageValidity; find='if key\.trust_status is EvidenceKeyTrustStatus\.REVOKED:'; repl='if False:'; tests=@($tCoverageValidity); select='test_one_revoked_member_invalidates_the_whole_claim' },
    @{ name='M17 an unknown key is not reported'; file=$coverageValidity; find='return \{StrategyCoverageValidityBlocker\.UNKNOWN_SIGNING_KEY\}'; repl='return set()'; tests=@($tCoverageValidity); select='test_an_unknown_member_key_invalidates_the_claim' },
    @{ name='M18 the evidence age bound is ignored'; file=$coverageValidity; find='and moment - generated_at > maximum_evidence_age'; repl='and False'; tests=@($tCoverageValidity); select='test_one_stale_member_invalidates_the_whole_claim' },
    @{ name='M19 version identity is not bound'; file=$coverageValidity; find='or record\.strategy_version_id != coverage\.strategy_version_id'; repl='or False'; tests=@($tCoverageValidity); select='test_an_authentication_whose_identity_drifted_invalidates_the_claim' },
    @{ name='M20 parameter hash is not bound'; file=$coverageValidity; find='or gate\.parameter_hash != coverage\.parameter_hash'; repl='or False'; tests=@($tCoverageValidity); select='test_a_gate_whose_identity_drifted_invalidates_the_claim' },
    @{ name='M21 the controller mutates lifecycle state'; file=$application; find='if not isinstance\(version, StrategyVersion\):'; repl="object.__setattr__(version, `"gate_passed`", True)`n        if not isinstance(version, StrategyVersion):"; tests=@($tEvaluator); select='test_the_controller_does_not_change_lifecycle_fields' },
    @{ name='M22 a pause can be justified by nothing'; file=$application; find='elif failures:\s*\n\s*blockers, triggers = set\(\), failures'; repl="elif failures:`n                blockers, triggers = failures, set()"; tests=@($tEvaluator); select='test_a_pause_is_authorised_by_a_named_governance_failure' },

    # -- the crash-safe protocol -------------------------------------------
    @{ name='M23 the decision is not persisted before the transition'; file=$application; find='self\._decisions\.record_decision\(outcome\.decision\)\s*\n\s*if outcome\.authorization is None:'; repl='if outcome.authorization is None:'; tests=@($tService); select='test_an_interruption_between_decide_and_apply_leaves_a_prepared_row' },
    @{ name='M24 the decision is never marked applied'; file=$application; find='applied = self\._decisions\.mark_applied\(\s*\n\s*outcome\.decision\.decision_id, applied_at=applied_at\s*\n\s*\)'; repl='applied = outcome.decision'; tests=@($tService); select='test_apply_prepares_then_transitions_then_marks_applied' },
    @{ name='M25 reconcile marks everything applied'; file=$application; find='if current is decision\.target_status:'; repl='if True:'; tests=@($tService); select='test_reconcile_leaves_an_unstarted_decision_prepared' },
    @{ name='M26 reconcile never supersedes'; file=$application; find='else:\s*\n\s*resolved\.append\(\s*\n\s*self\._decisions\.mark_superseded\(decision\.decision_id\)\s*\n\s*\)'; repl="else:`n                resolved.append(decision)"; tests=@($tService); select='test_reconcile_supersedes_a_decision_the_world_moved_past' },

    # -- persistence --------------------------------------------------------
    @{ name='M27 policy compare-and-set removed'; file=$repository; find='if current != expected_current_revision:'; repl='if False:'; tests=@($tRepository); select='test_cas_refuses_when_the_stored_history_has_a_hole' },
    @{ name='M28 decision payload hash check removed'; file=$repository; find='if sha256\(payload_json\.encode\("utf-8"\)\)\.hexdigest\(\) != payload_hash:'; repl='if False:'; tests=@($tRepository); select='test_corrupt_decision_payload_fails_closed' },
    @{ name='M29 decision indexed cross-check removed'; file=$repository; find='if indexed != stored:'; repl='if False:'; tests=@($tRepository); select='test_corrupt_indexed_columns_fail_closed' },
    @{ name='M30 retry idempotency broken by the decision clock'; file=$repository; find='payload\.pop\("authorized_at", None\)'; repl='None'; tests=@($tRepository); select='test_record_is_idempotent_across_clocks' },

    # -- architecture -------------------------------------------------------
    @{ name='M31 the validity validator imports the lifecycle adapter'; file=$coverageValidity; find='from us_quant\.trading\.domain\.strategy_gate import StrategyGateVerdict'; repl='import us_quant.trading.adapters.sqlite.strategy_lifecycle_repository as _adapter  # mutation'; tests=@($tCoverageValidityArchitecture); select='test_the_validator_reaches_no_adapter_and_no_authority' },
    @{ name='M32 lifecycle names a statistical threshold'; file=$application; find='class StrategyLifecycleController:'; repl="_STATISTICS_OWNER = `"MAXIMUM_PBO`"`n`n`nclass StrategyLifecycleController:"; tests=@($tArchitecture); select='test_l07_the_lifecycle_surface_never_recomputes_statistics' },
    @{ name='M33 a runtime module mints an authorization'; file=$runtimeConfig; find='from __future__ import annotations'; repl="from __future__ import annotations`nfrom us_quant.trading.domain.strategy_lifecycle import StrategyLifecycleAuthorization  # noqa: F401`n`n`ndef _forge():`n    return StrategyLifecycleAuthorization(None, None, None, None, None, _controller_token=object())"; tests=@($tArchitecture); select='test_l11_no_protected_layer_mints_an_authorization' },

    # -- the Paper launch gate ---------------------------------------------
    @{ name='M34 the plan boundary stops asking the launch gate'; file=$planBoundary; find='if \(\s*\n\s*self\._paper_authorization is None\s*\n\s*or not self\._paper_authorization\(version_id\)\s*\n\s*\):'; repl='if False:'; tests=@($tPortfolioOps); select='test_paper_shadow_alone_is_not_permission or test_a_plan_builder_without_an_authorizer_fails_closed' },
    @{ name='M35 a revoked key still authorises a launch'; file=$coverageValidity; find='if key\.trust_status is EvidenceKeyTrustStatus\.REVOKED:'; repl='if False:'; tests=@($tCoverageValidity); select='test_one_revoked_member_invalidates_the_whole_claim' },
    @{ name='M36 a failed authentication still authorises a launch'; file=$coverageValidity; find='if record\.verdict is not EvidenceAuthenticationVerdict\.PASS:'; repl='if False:'; tests=@($tCoverageValidity); select='test_an_authentication_that_no_longer_passes_invalidates_the_claim' },
    @{ name='M37 a failed coverage still authorises a launch'; file=$coverageValidity; find='if coverage\.verdict is not StrategyCoverageVerdict\.PASS:'; repl='if False:'; tests=@($tCoverageValidity); select='test_a_claim_that_is_not_pass_is_invalid' },
    @{ name='M38 a decision that never applied authorises a launch'; file=$launchGate; find='decision\.state is StrategyLifecycleDecisionState\.APPLIED'; repl='True'; tests=@($tLaunchGate); select='test_a_prepared_decision_is_not_authority or test_a_superseded_decision_is_not_authority' },
    @{ name='M39 a pause counts as an entry decision'; file=$launchGate; find='StrategyLifecycleAction\.PROMOTE_TO_PAPER_SHADOW,\s*\n\s*StrategyLifecycleAction\.RESUME_PAPER_SHADOW,'; repl="StrategyLifecycleAction.PROMOTE_TO_PAPER_SHADOW,`n        StrategyLifecycleAction.RESUME_PAPER_SHADOW,`n        StrategyLifecycleAction.PAUSE,"; tests=@($tLaunchGate); select='test_a_pause_alone_never_authorises_a_launch or test_pause_is_not_an_entry_action' },
    @{ name='M40 an unreadable authentication store raises instead of refusing'; file=$coverageValidity; find='except EvidenceAuthenticationRepositoryError:'; repl='except ZeroDivisionError:'; tests=@($tCoverageValidity); select='test_an_unreadable_authentication_store_invalidates_the_claim' },
    @{ name='M41 the plan boundary reads the legacy flag again'; file=$planBoundary; find='if version\.status is not StrategyStatus\.PAPER_SHADOW or version\.mode is not StrategyMode\.PAPER_SHADOW:'; repl="if version.status is not StrategyStatus.PAPER_SHADOW or version.mode is not StrategyMode.PAPER_SHADOW or not version.gate_passed:"; tests=@($tArchitecture); select='test_l20_the_plan_boundary_no_longer_reads_the_legacy_flag' },
    @{ name='M42 the trust root moves inside the runtime store'; file=$appPaths; find='return self\.state_root / "trust"'; repl='return self.runtime_root / "trust"'; tests=@($tArchitecture); select='test_l25_the_trust_root_stays_outside_the_runtime_store' }
)

function Get-Text([string]$path) { [System.IO.File]::ReadAllText($path) }
function Set-Text([string]$path, [string]$text) {
    [System.IO.File]::WriteAllText($path, $text, (New-Object System.Text.UTF8Encoding($false)))
}

if ($ValidateAnchors) {
    $bad = 0
    foreach ($mutation in $mutations) {
        $count = ([regex]::new($mutation.find)).Matches((Get-Text $mutation.file)).Count
        if ($count -ne 1) { $bad++ }
        Write-Host ("{0,3}  {1}" -f $count, $mutation.name)
    }
    Write-Host "anchors=$($mutations.Count) not_exactly_one=$bad"
    if ($bad -gt 0) { exit 1 }
    exit 0
}

$failures = @()
foreach ($mutation in $mutations) {
    $original = Get-Text $mutation.file
    $regex = [regex]::new($mutation.find)
    $matches = $regex.Matches($original)
    if ($matches.Count -ne 1) {
        $failures += "$($mutation.name): expected one mutation anchor, found $($matches.Count)"
        continue
    }
    $backup = "$($mutation.file).stage6c-backup"
    Copy-Item -LiteralPath $mutation.file -Destination $backup -Force
    try {
        Set-Text $mutation.file ($regex.Replace($original, $mutation.repl, 1))
        $previousPreference = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        $syntaxOutput = & $py -c "import ast,sys; ast.parse(open(sys.argv[1],encoding='utf-8').read())" $mutation.file 2>&1
        $syntaxExit = $LASTEXITCODE
        $ErrorActionPreference = $previousPreference
        if ($syntaxExit -ne 0) {
            $failures += "$($mutation.name): syntax error: $($syntaxOutput -join ' ')"
            Write-Host "RED=False $($mutation.name)"
            continue
        }
        $output = & $py -m pytest @($mutation.tests) -q -k $mutation.select 2>&1
        $exitCode = $LASTEXITCODE
        $outputText = $output | Out-String
        $collectionError = $outputText -match 'ERROR collecting|Interrupted: [0-9]+ errors during collection|ModuleNotFoundError|ImportError|SyntaxError'
        $testFailure = $outputText -match '(?m)(^|\s)[0-9]+ failed([,\s]|$)'
        $caught = ($exitCode -eq 1) -and $testFailure -and -not $collectionError
        Write-Host ("RED={0} {1}" -f [bool]$caught, $mutation.name)
        if (-not $caught) { $failures += "$($mutation.name): exit=$exitCode; $outputText" }
    }
    catch {
        $failures += "$($mutation.name): harness error: $_"
    }
    finally {
        $ErrorActionPreference = "Stop"
        Copy-Item -LiteralPath $backup -Destination $mutation.file -Force
        Remove-Item -LiteralPath $backup -Force
    }
}
Write-Host ("mutations={0} red={1} survivor_or_error={2}" -f $mutations.Count, ($mutations.Count - $failures.Count), $failures.Count)
foreach ($failure in $failures) { Write-Host "SURVIVED / HARNESS-ERROR: $failure" }
if ($failures.Count -gt 0) { exit 1 }
exit 0
