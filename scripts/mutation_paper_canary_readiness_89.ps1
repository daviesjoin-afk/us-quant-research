# Stage 6-D.5 Paper canary inspector semantic mutation gate.
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$env:PYTHONPATH = (Join-Path $projectRoot 'src') + ';' + (Join-Path $projectRoot 'tests')
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTHONUTF8 = '1'
$py = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $py)) { $py = (Get-Command python -ErrorAction Stop).Source }
$mutations = @(
    @{ name='C89-M01 expected runtime revision ignored'; file='src/us_quant/trading/application/paper_canary_readiness.py'; find='revision != self._spec.expected_runtime_revision'; repl='False'; test='tests/test_paper_canary_readiness_89.py'; select='expected_runtime_revision_mismatch' },
    @{ name='C89-M02 unknown revision substituted with expected'; file='src/us_quant/trading/application/paper_canary_readiness.py'; find='revision = current_runtime_revision or "UNKNOWN"'; repl='revision = current_runtime_revision or self._spec.expected_runtime_revision or "UNKNOWN"'; test='tests/test_paper_canary_readiness_89.py'; select='unknown_revision_fails_closed' },
    @{ name='C89-M03 missing evidence target accepted'; file='src/us_quant/trading/application/paper_canary_readiness.py'; find='elif any(item.status != "READY" for item in evidence):'; repl='elif False:'; test='tests/test_paper_canary_readiness_89.py'; select='four_of_five_targets_ready' },
    @{ name='C89-M04 Research version treated as Paper'; file='src/us_quant/trading/application/paper_canary_readiness.py'; find='strategy.status == StrategyStatus.PAPER_SHADOW.value'; repl='True'; test='tests/test_paper_canary_readiness_89.py'; select='research_version_never_counts' },
    @{ name='C89-M05 Research mode treated as Paper'; file='src/us_quant/trading/application/paper_canary_readiness.py'; find='and strategy.mode == StrategyMode.PAPER_SHADOW.value'; repl='and True'; test='tests/test_paper_canary_readiness_89.py'; select='status_with_research_mode' },
    @{ name='C89-M06 authorizer result forced true'; file='src/us_quant/trading/application/paper_canary_readiness.py'; find='authorized = bool(self._paper_launch_authorizer.authorises(version_id))'; repl='authorized = True'; test='tests/test_paper_canary_readiness_89.py'; select='stale_authorization_is_blocked' },
    @{ name='C89-M07 legacy gate flag grants authority'; file='src/us_quant/trading/application/paper_canary_readiness.py'; find='authorized = bool(self._paper_launch_authorizer.authorises(version_id))'; repl='authorized = bool(self._paper_launch_authorizer.authorises(version_id)) or version.gate_passed'; test='tests/test_paper_canary_readiness_89.py'; select='legacy_gate_passed_does_not_authorize' },
    @{ name='C89-M08 missing plan accepted'; file='src/us_quant/trading/application/paper_canary_readiness.py'; find='        if stored is None:'; repl='        if False:'; test='tests/test_paper_canary_readiness_89.py'; select='missing_portfolio_plan' },
    @{ name='C89-M09 canonical plan validation bypassed'; file='src/us_quant/trading/application/paper_canary_readiness.py'; find='plan = self._portfolio_plan_application.load()'; repl='plan = stored'; test='tests/test_paper_canary_readiness_89.py'; select='canonical_plan_validation_failure' },
    @{ name='C89-M10 invalid canonical plan marked valid'; file='src/us_quant/trading/application/paper_canary_readiness.py'; find='status="INVALID" if blockers else "VALID"'; repl='status="VALID"'; test='tests/test_paper_canary_readiness_89.py'; select='canonical_plan_validation_failure' },
    @{ name='C89-M11 plan subset accepted'; file='src/us_quant/trading/application/paper_canary_readiness.py'; find='return set(plan.selected_version_ids) == set(self._spec.strategy_version_ids)'; repl='return set(plan.selected_version_ids).issubset(set(self._spec.strategy_version_ids))'; test='tests/test_paper_canary_readiness_89.py'; select='subset_or_superset_is_rejected and selected0' },
    @{ name='C89-M12 plan superset accepted'; file='src/us_quant/trading/application/paper_canary_readiness.py'; find='return set(plan.selected_version_ids) == set(self._spec.strategy_version_ids)'; repl='return set(self._spec.strategy_version_ids).issubset(set(plan.selected_version_ids))'; test='tests/test_paper_canary_readiness_89.py'; select='subset_or_superset_is_rejected and selected1' },
    @{ name='C89-M13 shared SPY gets wrong parameters'; file='src/us_quant/trading/application/paper_canary_readiness.py'; find='parameters=version.parameters,'; repl='parameters=self._strategies.list_versions()[0].parameters,'; test='tests/test_paper_canary_readiness_89.py'; select='shared_spy_is_evaluated' },
    @{ name='C89-M14 target input order leaks into report'; file='src/us_quant/trading/application/paper_canary_readiness.py'; find='                sorted(`n                    self.evidence_targets,`n                    key=lambda item: (item.strategy_version_id, item.symbol),`n                )'; repl='                self.evidence_targets'; test='tests/test_paper_canary_readiness_89.py'; select='deterministic_and_input_order' },
    @{ name='C89-M15 lifecycle ordering uses ISO strings'; file='src/us_quant/trading/application/paper_canary_readiness.py'; find='return authorized_at.astimezone(UTC), decision.decision_id'; repl='return authorized_at.isoformat(), decision.decision_id'; test='tests/test_paper_canary_readiness_89.py'; select='lifecycle_latest_uses_utc' },
    @{ name='C89-M16 lifecycle same-instant tie is unstable'; file='src/us_quant/trading/application/paper_canary_readiness.py'; find='return authorized_at.astimezone(UTC), decision.decision_id'; repl='return authorized_at.astimezone(UTC), ""'; test='tests/test_paper_canary_readiness_89.py'; select='lifecycle_latest_uses_utc' },
    @{ name='C89-M17 missing performance blocks first launch'; file='src/us_quant/trading/application/paper_canary_readiness.py'; find='and strategy.paper_launch_authorized'; repl='and strategy.paper_launch_authorized and strategy.paper_performance_status is not PaperPerformanceObservation.NOT_YET_OBSERVED'; test='tests/test_paper_canary_readiness_89.py'; select='missing_performance_does_not_block' },
    @{ name='C89-M18 failing performance hidden'; file='src/us_quant/trading/application/paper_canary_readiness.py'; find='    verdict = getattr(evaluation.verdict, "value", str(evaluation.verdict)).upper()'; repl='    verdict = "PASS"'; test='tests/test_paper_canary_readiness_89.py'; select='performance_fail_is_displayed' },
    @{ name='C89-M19 offline declares canary ready'; file='src/us_quant/trading/application/paper_canary_readiness.py'; find='status = PaperCanaryInspectionStatus.READY_FOR_LIVE_PREFLIGHT'; repl='status = PaperCanaryInspectionStatus.READY_FOR_CANARY'; test='tests/test_paper_canary_readiness_89.py'; select='offline_never_claims_canary_ready' },
    @{ name='C89-M20 failed broker refresh accepted'; file='src/us_quant/trading/application/paper_canary_readiness.py'; find='            and broker.refresh_succeeded'; repl='            and True'; test='tests/test_paper_canary_readiness_89.py'; select='broker_refresh_failure' },
    @{ name='C89-M21 stale broker truth accepted'; file='src/us_quant/trading/application/paper_canary_readiness.py'; find='            and broker.freshness == "FRESH"'; repl='            and True'; test='tests/test_paper_canary_readiness_89.py'; select='stale_broker_truth' },
    @{ name='C89-M22 wrong environment accepted'; file='src/us_quant/trading/application/paper_canary_readiness.py'; find='            and (broker.broker_environment or "").lower() == "paper"'; repl='            and True'; test='tests/test_paper_canary_readiness_89.py'; select='broker_paper_environment_mismatch' },
    @{ name='C89-M23 missing account truth accepted'; file='src/us_quant/trading/application/paper_canary_readiness.py'; find='            and bool(broker.account_alias)'; repl='            and True'; test='tests/test_paper_canary_readiness_89.py'; select='broker_truth_without_safe_account_alias' },
    @{ name='C89-M24 unchecked reconciliation called clean'; file='src/us_quant/trading/application/paper_canary_readiness.py'; find='status="NOT_CHECKED"'; repl='status="CLEAN"'; test='tests/test_paper_canary_readiness_89.py'; select='broker_success_does_not_clear_unchecked' },
    @{ name='C89-M25 reconciliation blockers discarded'; file='src/us_quant/trading/application/paper_canary_readiness.py'; find='status="CLEAN" if not blockers else "BLOCKED"'; repl='status="CLEAN"'; test='tests/test_paper_canary_readiness_89.py'; select='reconciliation_blocker_is_preserved' },
    @{ name='C89-M26 reconciliation status bypassed'; file='src/us_quant/trading/application/paper_canary_readiness.py'; find='elif reconciliation.status != "CLEAN":'; repl='elif False:'; test='tests/test_paper_canary_readiness_89.py'; select='broker_success_does_not_clear_unchecked' },
    @{ name='C89-M27 target outside version accepted'; file='src/us_quant/trading/application/paper_canary_readiness.py'; find='        if any(`n            item.strategy_version_id not in self.strategy_version_ids`n            for item in self.evidence_targets`n        ):'; repl='        if False:'; test='tests/test_paper_canary_readiness_89.py'; select='spec_rejects_duplicate_targets' },
    @{ name='C89-M28 duplicate target accepted'; file='src/us_quant/trading/application/paper_canary_readiness.py'; find='if len(set(self.evidence_targets)) != len(self.evidence_targets):'; repl='if False:'; test='tests/test_paper_canary_readiness_89.py'; select='spec_rejects_duplicate_targets' },
    @{ name='C89-M29 quote store opened writable'; file='src/us_quant/trading/composition/paper_canary_readiness.py'; find='MinuteQuoteStore(quote_path, read_only=True)'; repl='MinuteQuoteStore(quote_path)'; test='tests/test_paper_canary_readiness_89.py'; select='market_quote_store_is_composed_in_read_only_mode' },
    @{ name='C89-M30 current Paper authorizer removed'; file='src/us_quant/trading/composition/paper_canary_readiness.py'; find='authorizer = PaperLaunchAuthorizer('; repl='authorizer = None # PaperLaunchAuthorizer('; test='tests/test_paper_canary_readiness_89.py'; select='composition_reuses_canonical_authorities' },
    @{ name='C89-M31 coverage current validator removed'; file='src/us_quant/trading/composition/paper_canary_readiness.py'; find='validity = StrategyCoverageCurrentValidator('; repl='validity = None # StrategyCoverageCurrentValidator('; test='tests/test_paper_canary_readiness_89.py'; select='composition_reuses_canonical_authorities' },
    @{ name='C89-M32 Qt dependency introduced'; file='src/us_quant/trading/application/paper_canary_readiness.py'; find='from __future__ import annotations'; repl='from __future__ import annotations`nif False:`n    import PySide6'; test='tests/test_paper_canary_readiness_89.py'; select='application_layer_has_no_shell' },
    @{ name='C89-M33 execution dependency introduced'; file='src/us_quant/trading/application/paper_canary_readiness.py'; find='from __future__ import annotations'; repl='from __future__ import annotations`nif False:`n    import us_quant.trading.application.execution'; test='tests/test_paper_canary_readiness_89.py'; select='application_layer_has_no_shell' },
    @{ name='C89-M34 shell dependency introduced in application'; file='src/us_quant/trading/application/paper_canary_readiness.py'; find='from __future__ import annotations'; repl='from __future__ import annotations`nif False:`n    import subprocess'; test='tests/test_paper_canary_readiness_89.py'; select='application_layer_has_no_shell' },
    @{ name='C89-M35 repository write introduced'; file='src/us_quant/trading/application/paper_canary_readiness.py'; find='stored = self._portfolio_plan_repository.load()'; repl='stored = self._portfolio_plan_repository.save()'; test='tests/test_paper_canary_readiness_89.py'; select='application_layer_has_no_shell' },
    @{ name='C89-M36 strategy transition introduced'; file='src/us_quant/trading/application/paper_canary_readiness.py'; find='authorized = bool(self._paper_launch_authorizer.authorises(version_id))'; repl='authorized = bool(self._strategies.transition(version_id))'; test='tests/test_paper_canary_readiness_89.py'; select='application_layer_has_no_shell' },
    @{ name='C89-M37 unsafe promotion CLI option introduced'; file='src/us_quant/paper_canary_readiness.py'; find='parser.add_argument("--live-broker-check", action="store_true")'; repl='parser.add_argument("--promote", action="store_true")`n    parser.add_argument("--live-broker-check", action="store_true")'; test='tests/test_paper_canary_readiness_89.py'; select='cli_shell_revision' },
    @{ name='C89-M38 SQLite readonly mode removed'; file='src/us_quant/sqlite_support.py'; find='def connect_sqlite_readonly(path: str | Path) -> sqlite3.Connection:`n    """Open an existing SQLite database without permitting any writes."""`n    database_uri = f"{Path(path).resolve().as_uri()}?mode=ro"`n    connection = sqlite3.connect(database_uri, timeout=30, uri=True)`n    connection.execute("PRAGMA busy_timeout = 30000")`n    connection.execute("PRAGMA query_only = ON")`n    return connection'; repl='def connect_sqlite_readonly(path: str | Path) -> sqlite3.Connection:`n    return connect_sqlite(path)'; test='tests/test_paper_canary_readiness_89.py'; select='readonly_sqlite_connection_refuses_writes' },
    @{ name='C89-M39 6-F blocker hidden'; file='src/us_quant/trading/application/paper_canary_readiness.py'; find='stage6_f_blocker="Stage 6-D supervised operational canary incomplete"'; repl='stage6_f_blocker=""'; test='tests/test_paper_canary_readiness_89.py'; select='application_performs_no_repository_mutations' },
    @{ name='C89-M40 AI/evolution import introduced'; file='src/us_quant/trading/application/paper_canary_readiness.py'; find='from __future__ import annotations'; repl='from __future__ import annotations`nif False:`n    import us_quant.trading.application.evolution'; test='tests/test_paper_canary_readiness_89.py'; select='application_layer_has_no_shell' }
)
$baseline = & $py -m pytest (Join-Path $projectRoot 'tests/test_paper_canary_readiness_89.py') -q 2>&1
if ($LASTEXITCODE -ne 0) { Write-Host ($baseline -join "`n"); throw 'Baseline is not GREEN' }
$survivors = @()
$errors = @()
foreach ($mutation in $mutations) {
    $path = [IO.Path]::GetFullPath((Join-Path $projectRoot $mutation.file))
    if (-not $path.StartsWith([IO.Path]::GetFullPath($projectRoot) + [IO.Path]::DirectorySeparatorChar)) { throw 'Mutation path escaped workspace' }
    $original = [IO.File]::ReadAllText($path).Replace("`r`n", "`n")
    $anchor = $mutation.find.Replace('`n', "`n")
    $replacement = $mutation.repl.Replace('`n', "`n")
    $count = [regex]::Matches($original, [regex]::Escape($anchor)).Count
    if ($count -ne 1) { $errors += "$($mutation.name): anchors=$count"; continue }
    try {
        [IO.File]::WriteAllText($path, $original.Replace($anchor, $replacement), [Text.UTF8Encoding]::new($false))
        $output = & $py -m pytest (Join-Path $projectRoot $mutation.test) -q -k $mutation.select --tb=short 2>&1
        $code = $LASTEXITCODE
        if ($code -eq 1 -and ($output | Select-String -Pattern 'FAILED|AssertionError|DID NOT RAISE|NameError|TypeError|ValueError|IntegrityError' -Quiet) -and -not ($output | Select-String -Pattern 'no tests ran|ERROR collecting' -Quiet)) {
            Write-Host "RED=True $($mutation.name)"
        } elseif ($code -eq 0) {
            $survivors += $mutation.name
            Write-Host "SURVIVOR $($mutation.name)"
        } else {
            $errors += "$($mutation.name): exit=$code; $($output -join ' ')"
        }
    } finally {
        [IO.File]::WriteAllText($path, $original, [Text.UTF8Encoding]::new($false))
    }
}
Write-Host "mutations=$($mutations.Count) red=$($mutations.Count-$survivors.Count-$errors.Count) survivors=$($survivors.Count) harness_errors=$($errors.Count)"
foreach ($item in ($survivors + $errors)) { Write-Host $item }
if ($survivors.Count -gt 0 -or $errors.Count -gt 0) { exit 1 }
exit 0
