$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$env:PYTHONPATH = (Join-Path $projectRoot 'src') + ';' + (Join-Path $projectRoot 'tests')
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTHONUTF8 = '1'
$py = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $py)) { $py = (Get-Command python -ErrorAction Stop).Source }
$mutations = @(
    @{ name='M01 ignore replay blockers'; file='src/us_quant/trading/application/paper_canary_operational_proof.py'; find='if replay.blockers:'; repl='if False:'; test='tests/test_paper_canary_operational_proof.py'; select='duplicate_execution_id' },
    @{ name='M02 ignore missing attribution'; file='src/us_quant/trading/application/paper_canary_operational_proof.py'; find='block(ProofStatus.INCOMPLETE_SESSION_TRUTH, "missing_execution_attribution")'; repl='pass'; test='tests/test_paper_canary_operational_proof.py'; select='missing_attribution_blocks' },
    @{ name='M03 ignore attribution completeness'; file='src/us_quant/trading/application/paper_canary_operational_proof.py'; find='if not replay.attribution_complete:'; repl='if False:'; test='tests/test_paper_canary_operational_proof.py'; select='missing_attribution_blocks' },
    @{ name='M04 ignore reconciliation blockers'; file='src/us_quant/trading/application/paper_canary_operational_proof.py'; find='if reconciliation.blockers:'; repl='if False:'; test='tests/test_paper_canary_operational_proof.py'; select='unexplained_broker_order' },
    @{ name='M05 assume absent broker is clean'; file='src/us_quant/trading/application/paper_canary_operational_proof.py'; find='block(ProofStatus.RECONCILIATION_BLOCKED, "broker_observation_missing")'; repl='pass'; test='tests/test_paper_canary_operational_proof.py'; select='missing_broker_observation' },
    @{ name='M06 accept zero fills'; file='src/us_quant/trading/application/paper_canary_operational_proof.py'; find='if not campaign_decisions or not campaign_orders or not fills:'; repl='if not campaign_decisions or not campaign_orders:'; test='tests/test_paper_canary_operational_proof.py'; select='no_fills_cannot_pass' },
    @{ name='M07 accept empty canary'; file='src/us_quant/trading/application/paper_canary_operational_proof.py'; find='if not campaign_decisions and not campaign_orders:'; repl='if False:'; test='tests/test_paper_canary_operational_proof.py'; select='empty_stores' },
    @{ name='M08 accept no performance'; file='src/us_quant/trading/application/paper_canary_operational_proof.py'; find='block(ProofStatus.PERFORMANCE_MISSING, "exact_performance_strategy_set_required")'; repl='pass'; test='tests/test_paper_canary_operational_proof.py'; select='no_performance_evaluation' },
    @{ name='M09 ignore performance session links'; file='src/us_quant/trading/application/paper_canary_operational_proof.py'; find='block(ProofStatus.PERFORMANCE_MISSING, "performance_session_linkage_mismatch")'; repl='pass'; test='tests/test_paper_canary_operational_proof.py'; select='performance_links_must_match' },
    @{ name='M10 ignore exact strategy targets'; file='src/us_quant/trading/application/paper_canary_operational_proof.py'; find='if present_versions != versions:'; repl='if False:'; test='tests/test_paper_canary_operational_proof.py'; select='exact_target_identity_mismatch' },
    @{ name='M11 ignore exact session targets'; file='src/us_quant/trading/application/paper_canary_operational_proof.py'; find='if present_sessions != sessions:'; repl='if False:'; test='tests/test_paper_canary_operational_proof.py'; select='exact_target_identity_mismatch' },
    @{ name='M12 ignore runtime revision'; file='src/us_quant/trading/application/paper_canary_operational_proof.py'; find='block(ProofStatus.RESTART_MISMATCH, "runtime_revision_mismatch")'; repl='pass'; test='tests/test_paper_canary_operational_proof.py'; select='runtime_revision_mismatch' },
    @{ name='M13 ignore persisted economics mismatch'; file='src/us_quant/trading/application/paper_canary_operational_proof.py'; find='block(ProofStatus.PERFORMANCE_MISSING, "performance_metrics_replay_mismatch")'; repl='pass'; test='tests/test_paper_canary_operational_proof.py'; select='performance_metrics_must_match' },
    @{ name='M14 ignore persisted source linkage'; file='src/us_quant/trading/application/paper_canary_operational_proof.py'; find='block(ProofStatus.PERFORMANCE_MISSING, "performance_source_linkage_mismatch")'; repl='pass'; test='tests/test_paper_canary_operational_proof.py'; select='performance_links_must_match' },
    @{ name='M15 ignore canonical source replay'; file='src/us_quant/trading/application/paper_canary_operational_proof.py'; find='block(ProofStatus.PERFORMANCE_MISSING, "performance_source_replay_mismatch")'; repl='pass'; test='tests/test_paper_canary_operational_proof.py'; select='performance_links_must_match' },
    @{ name='M16 accept blocked current proof'; file='src/us_quant/trading/application/paper_canary_operational_proof.py'; find='if current.blockers:'; repl='if False:'; test='tests/test_paper_canary_operational_proof.py'; select='missing_or_ineligible_restart_baseline' },
    @{ name='M17 accept ineligible baseline'; file='src/us_quant/trading/application/paper_canary_operational_proof.py'; find='if baseline.get("snapshot_status") != ProofStatus.RESTART_BASELINE_MISSING or baseline.get("snapshot_blockers") != []:'; repl='if False:'; test='tests/test_paper_canary_operational_proof.py'; select='missing_or_ineligible_restart_baseline' },
    @{ name='M18 claim restart without baseline'; file='src/us_quant/trading/application/paper_canary_operational_proof.py'; find='return replace(current, status=ProofStatus.RESTART_BASELINE_MISSING,`n                       blockers=tuple(sorted(set(current.blockers) | {"restart_baseline_missing"})))'; repl='return replace(current, status=ProofStatus.OPERATIONAL_PROOF_PASS, blockers=())'; test='tests/test_paper_canary_operational_proof.py'; select='missing_or_ineligible_restart_baseline' },
    @{ name='M19 ignore changed durable semantics'; file='src/us_quant/trading/application/paper_canary_operational_proof.py'; find='if canonical_json(baseline.get(key)) != canonical_json(current.semantic_projection(mode)):'; repl='if False:'; test='tests/test_paper_canary_operational_proof.py'; select='changed_durable_projection' },
    @{ name='M20 require old digest for fresh observation'; file='src/us_quant/trading/application/paper_canary_operational_proof.py'; find='canonical_json(current.semantic_projection(mode))'; repl='canonical_json(current.semantic_projection(PerformanceComparison.DURABLE_RECOVERY))'; test='tests/test_paper_canary_operational_proof.py'; select='fresh_reconstruction_accepts' },
    @{ name='M21 ignore fresh metrics'; file='src/us_quant/trading/application/paper_canary_operational_proof.py'; find='"metrics": evaluation.metrics, "verdict": evaluation.verdict,'; repl='"metrics": None, "verdict": evaluation.verdict,'; test='tests/test_paper_canary_operational_proof.py'; select='performance_changed_semantics_rejected' },
    @{ name='M22 ignore fresh verdict and blockers'; file='src/us_quant/trading/application/paper_canary_operational_proof.py'; find='"metrics": evaluation.metrics, "verdict": evaluation.verdict,`n        "blockers": sorted(evaluation.blockers),'; repl='"metrics": evaluation.metrics, "verdict": None,`n        "blockers": [],'; test='tests/test_paper_canary_operational_proof.py'; select='performance_changed_semantics_rejected' },
    @{ name='M23 digest drops fills'; file='src/us_quant/trading/application/paper_canary_operational_proof.py'; find='"events": _sorted_values(x.events), "fills": _sorted_values(x.fills),'; repl='"events": _sorted_values(x.events), "fills": (),'; test='tests/test_paper_canary_operational_proof.py'; select='business_fact_payload_changes_digest' },
    @{ name='M24 digest drops attribution'; file='src/us_quant/trading/application/paper_canary_operational_proof.py'; find='"attributions": _sorted_values(attrs)'; repl='"attributions": ()'; test='tests/test_paper_canary_operational_proof.py'; select='business_fact_payload_changes_digest' },
    @{ name='M25 digest drops decision'; file='src/us_quant/trading/application/paper_canary_operational_proof.py'; find='"decisions": _sorted_values(decisions)'; repl='"decisions": ()'; test='tests/test_paper_canary_operational_proof.py'; select='business_fact_payload_changes_digest' },
    @{ name='M26 digest drops order intent'; file='src/us_quant/trading/application/paper_canary_operational_proof.py'; find='"intent": x.intent, "account_alias": x.account_alias, "broker_order_id": x.broker_order_id,'; repl='"intent": None, "account_alias": x.account_alias, "broker_order_id": x.broker_order_id,'; test='tests/test_paper_canary_operational_proof.py'; select='business_fact_payload_changes_digest' },
    @{ name='M27 digest uses unordered attribution'; file='src/us_quant/trading/application/paper_canary_operational_proof.py'; find='"attributions": _sorted_values(attrs)'; repl='"attributions": attrs'; test='tests/test_paper_canary_operational_proof.py'; select='row_read_order_and_utc' },
    @{ name='M28 digest binds current clock'; file='src/us_quant/trading/application/paper_canary_operational_proof.py'; find='truth_digest = digest({'; repl='truth_digest = digest({"wall_clock": now,'; test='tests/test_paper_canary_operational_proof.py'; select='row_read_order_and_utc' },
    @{ name='M29 skip baseline checksum'; file='src/us_quant/paper_canary_operational_proof.py'; find='if digest(payload) != value["artifact_sha256"]:'; repl='if False:'; test='tests/test_paper_canary_operational_proof.py'; select='edited_or_malformed_artifact' },
    @{ name='M30 overwrite existing artifact'; file='src/us_quant/paper_canary_operational_proof.py'; find='        if path.exists():'; repl='        if False:'; test='tests/test_paper_canary_operational_proof.py'; select='baseline_self_hash_and_write_once' },
    @{ name='M31 accept unknown artifact schema'; file='src/us_quant/paper_canary_operational_proof.py'; find='if value["schema_version"] != SCHEMA_VERSION:'; repl='if False:'; test='tests/test_paper_canary_operational_proof.py'; select='edited_or_malformed_artifact' },
    @{ name='M32 readonly portfolio initializes store'; file='src/us_quant/trading/adapters/sqlite/portfolio_repository.py'; find='        if read_only:`n            return'; repl='        if False:`n            return'; test='tests/test_paper_canary_operational_proof.py'; select='readonly_repositories' },
    @{ name='M33 readonly orders permit writes'; file='src/us_quant/trading/adapters/sqlite/order_repository.py'; find='        if self._read_only:`n            return connect_sqlite_readonly(self.path)'; repl='        if False:`n            return connect_sqlite_readonly(self.path)'; test='tests/test_paper_canary_operational_proof.py'; select='readonly_repositories' },
    @{ name='M34 auditor imports execution owner'; file='src/us_quant/trading/application/paper_canary_operational_proof.py'; find='from enum import StrEnum'; repl='from enum import StrEnum`nfrom us_quant.trading.application.execution import ExecutionApplication'; test='tests/test_paper_canary_operational_proof.py'; select='architecture_proof_has_only_read_ports' },
    @{ name='M35 auditor writes business repository'; file='src/us_quant/trading/application/paper_canary_operational_proof.py'; find='        aliases = {x.account_alias for x in orders.orders if x.account_alias}'; repl='        self._portfolio.record_decision(decisions[0])`n        aliases = {x.account_alias for x in orders.orders if x.account_alias}'; test='tests/test_paper_canary_operational_proof.py'; select='application_is_read_only_and_no_runtime_authority' },
    @{ name='M36 auditor imports lifecycle writer'; file='src/us_quant/trading/application/paper_canary_operational_proof.py'; find='from enum import StrEnum'; repl='from enum import StrEnum`nfrom us_quant.trading.application.strategies import StrategyApplication'; test='tests/test_paper_canary_operational_proof.py'; select='architecture_proof_has_only_read_ports' },
    @{ name='M37 auditor arms broker'; file='src/us_quant/trading/application/paper_canary_operational_proof.py'; find='        aliases = {x.account_alias for x in orders.orders if x.account_alias}'; repl='        broker.arm()`n        aliases = {x.account_alias for x in orders.orders if x.account_alias}'; test='tests/test_paper_canary_operational_proof.py'; select='architecture_proof_has_only_read_ports' },
    @{ name='M38 ignore restart open orders'; file='src/us_quant/trading/application/paper_canary_operational_proof.py'; find='"open_order_ids": self.open_order_ids, "replay_blockers": self.replay_blockers,'; repl='"open_order_ids": (), "replay_blockers": self.replay_blockers,'; test='tests/test_paper_canary_operational_proof.py'; select='changed_durable_projection' },
    @{ name='M39 accept duplicate spec identities'; file='src/us_quant/trading/application/paper_canary_operational_proof.py'; find='or len(set(values)) != len(values)'; repl='or False'; test='tests/test_paper_canary_operational_proof.py'; select='spec_rejects_duplicate_identities' },
    @{ name='M40 accept evaluation before last fill'; file='src/us_quant/trading/application/paper_canary_operational_proof.py'; find='block(ProofStatus.PERFORMANCE_MISSING, "performance_does_not_cover_all_session_fills")'; repl='pass'; test='tests/test_paper_canary_operational_proof.py'; select='evaluation_before_last_session_fill' },
    @{ name='M41 ignore stable semver'; file='src/us_quant/trading/application/paper_canary_operational_proof.py'; find='version.version_id, version.semver, version.parameter_hash,'; repl='version.version_id, "fixed-semver", version.parameter_hash,'; test='tests/test_paper_canary_operational_proof.py'; select='fresh_comparison_binds_stable_strategy_source' },
    @{ name='M42 ignore stable parameter hash'; file='src/us_quant/trading/application/paper_canary_operational_proof.py'; find='version.version_id, version.semver, version.parameter_hash,'; repl='version.version_id, version.semver, "fixed-parameter-hash",'; test='tests/test_paper_canary_operational_proof.py'; select='fresh_comparison_binds_stable_strategy_source' },
    @{ name='M43 ignore stable universe hash'; file='src/us_quant/trading/application/paper_canary_operational_proof.py'; find='version.universe_hash, version.code_hash,'; repl='"fixed-universe-hash", version.code_hash,'; test='tests/test_paper_canary_operational_proof.py'; select='fresh_comparison_binds_stable_strategy_source' },
    @{ name='M44 ignore stable code hash'; file='src/us_quant/trading/application/paper_canary_operational_proof.py'; find='version.universe_hash, version.code_hash,'; repl='version.universe_hash, "fixed-code-hash",'; test='tests/test_paper_canary_operational_proof.py'; select='fresh_comparison_binds_stable_strategy_source' },
    @{ name='M45 accept ancestor git root'; file='src/us_quant/paper_canary_operational_proof.py'; find='if Path(top_level.stdout.strip()).resolve() != root.resolve():'; repl='if False:'; test='tests/test_paper_canary_operational_proof.py'; select='runtime_revision_uses_exact_resource_root' },
    @{ name='M46 resolve source install path instead of resource root'; file='src/us_quant/paper_canary_operational_proof.py'; find='root = ApplicationPaths.discover().resource_root'; repl='root = Path(__file__).resolve().parents[2]'; test='tests/test_paper_canary_operational_proof.py'; select='runtime_revision_uses_exact_resource_root' },
    @{ name='M47 fresh CLI silently uses baseline evaluations'; file='src/us_quant/paper_canary_operational_proof.py'; find='and not args.evaluation_id):'; repl='and False):'; test='tests/test_paper_canary_operational_proof.py'; select='cli_fresh_requires_explicit_new_evaluations' },
    @{ name='M48 fresh comparison reuses baseline evaluations'; file='src/us_quant/trading/application/paper_canary_operational_proof.py'; find='if mode is PerformanceComparison.FRESH_RECONSTRUCTION and ('; repl='if False and ('; test='tests/test_paper_canary_operational_proof.py'; select='fresh_comparison_rejects_reused_evaluations' },
    @{ name='M49 ignore requested missing policy'; file='src/us_quant/trading/application/paper_canary_operational_proof.py'; find='"requested_policy_id": evaluation.requested_policy_id,'; repl='"requested_policy_id": None,'; test='tests/test_paper_canary_operational_proof.py'; select='fresh_comparison_binds_requested_missing_policy' },
    @{ name='M50 accept unknown runtime in inspection'; file='src/us_quant/trading/application/paper_canary_operational_proof.py'; find='if not _runtime_revision_known(runtime_revision):'; repl='if False:'; test='tests/test_paper_canary_operational_proof.py'; select='unknown_runtime_revision_blocks_inspection' },
    @{ name='M51 accept unknown baseline and current runtime'; file='src/us_quant/trading/application/paper_canary_operational_proof.py'; find='if not _runtime_revision_known(baseline.get("runtime_revision")) or not _runtime_revision_known(current.runtime_revision):'; repl='if False:'; test='tests/test_paper_canary_operational_proof.py'; select='unknown_runtime_revision_blocks_comparison' },
    @{ name='M52 accept preexisting distinct evaluations as fresh'; file='src/us_quant/trading/application/paper_canary_operational_proof.py'; find='if any(x.evaluated_at <= generated_at for x in current.performance_evaluations):'; repl='if False:'; test='tests/test_paper_canary_operational_proof.py'; select='fresh_evaluations_must_postdate_baseline' },
    @{ name='M53 accept evaluation at snapshot time as fresh'; file='src/us_quant/trading/application/paper_canary_operational_proof.py'; find='x.evaluated_at <= generated_at'; repl='x.evaluated_at < generated_at'; test='tests/test_paper_canary_operational_proof.py'; select='fresh_evaluations_must_postdate_baseline' },
    @{ name='M54 accept mixed preexisting and new evaluations'; file='src/us_quant/trading/application/paper_canary_operational_proof.py'; find='if any(x.evaluated_at <= generated_at for x in current.performance_evaluations):'; repl='if all(x.evaluated_at <= generated_at for x in current.performance_evaluations):'; test='tests/test_paper_canary_operational_proof.py'; select='fresh_evaluations_must_postdate_baseline' },
    @{ name='M55 timestamp snapshot before inspection'; file='src/us_quant/paper_canary_operational_proof.py'; find='make_artifact(proof, generated_at=datetime.now(UTC))'; repl='make_artifact(proof, generated_at=now)'; test='tests/test_paper_canary_operational_proof.py'; select='snapshot_boundary_is_after_inspection' },
    @{ name='M56 reject pre-window basis with campaign-only equality'; file='src/us_quant/trading/application/paper_canary_operational_proof.py'; find='if not {x.execution_id for x in fills} <= {e for x in evaluations for e in x.source_execution_ids}:'; repl='if {x.execution_id for x in fills} != {e for x in evaluations for e in x.source_execution_ids}:'; test='tests/test_paper_canary_operational_proof.py'; select='pre_window_basis_sources_are_valid' },
    @{ name='M57 reject valid pre-window source linkage'; file='src/us_quant/trading/application/paper_canary_operational_proof.py'; find='if (not set(evaluation.source_portfolio_decision_ids) <= {x.decision.decision_id for x in decisions}`n                    or not set(evaluation.source_order_ids) <= {x.intent.order_id for x in orders.orders if x.intent}`n                    or not set(evaluation.source_execution_ids) <= {f.execution_id for x in orders.orders for f in x.fills}):'; repl='if (not set(evaluation.source_portfolio_decision_ids) <= {x.decision.decision_id for x in campaign_decisions}`n                    or not set(evaluation.source_order_ids) <= order_ids`n                    or not set(evaluation.source_execution_ids) <= {x.execution_id for x in fills}):'; test='tests/test_paper_canary_operational_proof.py'; select='pre_window_basis_sources_are_valid' }
)
$baselineTargets = @(
    $mutations |
        ForEach-Object { "$($_.test)|$($_.select)" } |
        Sort-Object -Unique
)
foreach ($target in $baselineTargets) {
    $parts = $target.Split('|', 2)
    $testPath = Join-Path $projectRoot $parts[0]
    $selector = $parts[1]
    $baseline = & $py -m pytest $testPath -q -k $selector --tb=short 2>&1
    $code = $LASTEXITCODE
    $outputText = $baseline -join "`n"
    $executed = 0
    foreach ($match in [regex]::Matches($outputText, '(\d+) passed')) {
        $executed += [int]$match.Groups[1].Value
    }
    if ($code -ne 0 -or $executed -lt 1 -or $outputText -match 'no tests ran|ERROR collecting') {
        Write-Host $outputText
        throw "Baseline is not GREEN: $($parts[0]) -k $selector (exit=$code, executed=$executed)"
    }
    Write-Host "BASELINE GREEN $($parts[0]) -k $selector (executed=$executed)"
}
$survivors = @()
$errors = @()
foreach ($mutation in $mutations) {
    $path = [IO.Path]::GetFullPath((Join-Path $projectRoot $mutation.file))
    if (-not $path.StartsWith([IO.Path]::GetFullPath($projectRoot) + [IO.Path]::DirectorySeparatorChar)) { throw 'Mutation path escaped workspace' }
    $originalBytes = [IO.File]::ReadAllBytes($path)
    $original = [IO.File]::ReadAllText($path).Replace("`r`n", "`n")
    $anchor = $mutation.find.Replace('`n', "`n")
    $replacement = $mutation.repl.Replace('`n', "`n")
    $count = [regex]::Matches($original, [regex]::Escape($anchor)).Count
    if ($count -ne 1) { $errors += "$($mutation.name): anchors=$count"; continue }
    try {
        [IO.File]::WriteAllText($path, $original.Replace($anchor, $replacement), [Text.UTF8Encoding]::new($false))
        $output = & $py -m pytest (Join-Path $projectRoot $mutation.test) -q -k $mutation.select --tb=short 2>&1
        $code = $LASTEXITCODE
        if ($code -eq 1 -and ($output | Select-String -Pattern 'FAILED|AssertionError|DID NOT RAISE|IntegrityError' -Quiet) -and -not ($output | Select-String -Pattern 'no tests ran|ERROR collecting' -Quiet)) {
            Write-Host "RED=True $($mutation.name)"
        } elseif ($code -eq 0) {
            $survivors += $mutation.name
            Write-Host "SURVIVOR $($mutation.name)"
        } else {
            $errors += "$($mutation.name): exit=$code; $($output -join ' ')"
        }
    } finally {
        [IO.File]::WriteAllBytes($path, $originalBytes)
    }
}
Write-Host "mutations=$($mutations.Count) red=$($mutations.Count-$survivors.Count-$errors.Count) survivors=$($survivors.Count) harness_errors=$($errors.Count)"
foreach ($item in ($survivors + $errors)) { Write-Host $item }
if ($survivors.Count -gt 0 -or $errors.Count -gt 0) { exit 1 }
exit 0
