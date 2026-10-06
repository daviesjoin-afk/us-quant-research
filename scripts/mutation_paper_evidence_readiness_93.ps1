# PR #93 Paper evidence readiness semantic mutation gate.
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$env:PYTHONPATH = (Join-Path $projectRoot 'src') + ';' + (Join-Path $projectRoot 'tests')
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTHONUTF8 = '1'
$py = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $py)) { $py = (Get-Command python -ErrorAction Stop).Source }
$APP = 'src/us_quant/trading/application/paper_evidence_readiness.py'
$COMP = 'src/us_quant/trading/composition/paper_evidence_readiness.py'
$CLI = 'src/us_quant/paper_evidence_readiness.py'
$TEST = 'tests/test_paper_evidence_readiness.py'
$mutations = @(
    @{ name='R93-M01 ignore disconnected IBKR socket'; file=$APP; find='    if not ibkr.socket_reachable:\n        blockers.append("IBKR_SOCKET_UNREACHABLE")'; repl='    if False:\n        blockers.append("IBKR_SOCKET_UNREACHABLE")'; test=$TEST; select='r93_01_ibkr_disconnected_is_blocked' },
    @{ name='R93-M02 unchecked IBKR treated as checked'; file=$APP; find='    if not ibkr.checked:\n        return ("IBKR_NOT_CHECKED",)'; repl='    if False:\n        return ("IBKR_NOT_CHECKED",)'; test=$TEST; select='r93_01b_unchecked_ibkr_fails_closed' },
    @{ name='R93-M03 ignore disconnected market stream'; file=$APP; find='    if not stream.connected:\n        blockers.append("MARKET_STREAM_DISCONNECTED")'; repl='    if False:\n        blockers.append("MARKET_STREAM_DISCONNECTED")'; test=$TEST; select='r93_02b_disconnected_or_stalled_stream_is_blocked' },
    @{ name='R93-M04 ignore realtime flag'; file=$APP; find='    if not stream.realtime:\n        blockers.append("MARKET_STREAM_NOT_REALTIME")'; repl='    if False:\n        blockers.append("MARKET_STREAM_NOT_REALTIME")'; test=$TEST; select='r93_02_connected_but_delayed_data_is_blocked' },
    @{ name='R93-M05 accept delayed feed as realtime'; file=$APP; find='    if not stream.realtime:\n        blockers.append("MARKET_STREAM_NOT_REALTIME")'; repl='    if stream.realtime is None:\n        blockers.append("MARKET_STREAM_NOT_REALTIME")'; test=$TEST; select='r93_02_connected_but_delayed_data_is_blocked' },
    @{ name='R93-M06 ignore stalled stream flag'; file=$APP; find='    if stream.stalled or _snapshot_is_stale(stream.last_snapshot_at, now):\n        blockers.append("MARKET_STREAM_STALLED")'; repl='    if False:\n        blockers.append("MARKET_STREAM_STALLED")'; test=$TEST; select='r93_02b_disconnected_or_stalled_stream_is_blocked' },
    @{ name='R93-M07 ignore stale health snapshot age'; file=$APP; find='    return age > STREAM_STALE_AFTER_SECONDS'; repl='    return False'; test=$TEST; select='r93_02c_stale_health_snapshot_is_blocked' },
    @{ name='R93-M08 accept an unobserved stream'; file=$APP; find='    if not stream.observed:\n        return ("MARKET_STREAM_NOT_OBSERVED",)'; repl='    if False:\n        return ("MARKET_STREAM_NOT_OBSERVED",)'; test=$TEST; select='r93_02d_unobserved_stream_is_blocked' },
    @{ name='R93-M09 ignore stream provider mismatch'; file=$APP; find='    if stream.provider.strip().upper() != provider.strip().upper():\n        blockers.append("PROVIDER_MISMATCH")'; repl='    if False:\n        blockers.append("PROVIDER_MISMATCH")'; test=$TEST; select='r93_08_provider_mismatch_is_blocked' },
    @{ name='R93-M10 ignore evidence provider mismatch'; file=$APP; find='                "PROVIDER_MISMATCH" in item.quality_blockers for item in invalid'; repl='                False for item in invalid'; test=$TEST; select='r93_08b_evidence_provider_mismatch_is_blocked' },
    @{ name='R93-M11 ignore live API port'; file=$APP; find='    if ibkr.port in LIVE_API_PORTS:\n        blockers.append("IBKR_LIVE_PORT")'; repl='    if False:\n        blockers.append("IBKR_LIVE_PORT")'; test=$TEST; select='r93_07_live_port_is_blocked' },
    @{ name='R93-M12 accept a non-paper port'; file=$APP; find='    elif ibkr.port not in PAPER_API_PORTS:\n        blockers.append("IBKR_PORT_NOT_PAPER")'; repl='    elif False:\n        blockers.append("IBKR_PORT_NOT_PAPER")'; test=$TEST; select='r93_07c_non_paper_port_is_blocked' },
    @{ name='R93-M13 ignore order submission enabled'; file=$APP; find='    if ibkr.paper_order_submission_enabled:\n        blockers.append("IBKR_ORDER_SUBMISSION_ENABLED")'; repl='    if False:\n        blockers.append("IBKR_ORDER_SUBMISSION_ENABLED")'; test=$TEST; select='r93_06_order_submission_enabled_is_blocked' },
    @{ name='R93-M14 ignore non-read-only API'; file=$APP; find='    if not ibkr.api_read_only:\n        blockers.append("IBKR_API_NOT_READ_ONLY")'; repl='    if False:\n        blockers.append("IBKR_API_NOT_READ_ONLY")'; test=$TEST; select='r93_06_order_submission_enabled_is_blocked' },
    @{ name='R93-M15 zero rows classified as complete'; file=$APP; find='        elif all(item.captured_session_count == 0 for item in targets):'; repl='        elif False:'; test=$TEST; select='r93_03_realtime_stream_with_zero_rows_is_data_collection' },
    @{ name='R93-M16 ignore incomplete sessions'; file=$APP; find='        elif any(not item.complete for item in targets):'; repl='        elif False:'; test=$TEST; select='r93_04_one_symbol_incomplete_is_blocked' },
    @{ name='R93-M17 ignore invalid evidence rows'; file=$APP; find='        elif any(item.status == EVIDENCE_INVALID_STATUS for item in targets):'; repl='        elif False:'; test=$TEST; select='r93_08b_evidence_provider_mismatch_is_blocked' },
    @{ name='R93-M18 ignore target set mismatch'; file=$APP; find='        elif tuple(item.symbol for item in targets) != self._spec.symbols:'; repl='        elif False:'; test=$TEST; select='r93_08c_target_set_mismatch_is_blocked' },
    @{ name='R93-M19 every target marked complete'; file=$APP; find='            complete=row.status == EVIDENCE_READY_STATUS,'; repl='            complete=True,'; test=$TEST; select='r93_04_one_symbol_incomplete_is_blocked' },
    @{ name='R93-M20 data collection displayed as READY'; file=$APP; find='    PaperEvidenceReadinessStatus.DATA_COLLECTION: "BLOCKED_DATA_COLLECTION",'; repl='    PaperEvidenceReadinessStatus.DATA_COLLECTION: "READY",'; test=$TEST; select='r93_cli_reports_blocked_data_collection' },
    @{ name='R93-M21 unavailable evidence store ignored'; file=$APP; find='        if evidence_unavailable_reason is not None:\n            blockers.append(evidence_unavailable_reason)'; repl='        if False:\n            blockers.append(evidence_unavailable_reason)'; test=$TEST; select='r93_09_unavailable_store_is_blocked' },
    @{ name='R93-M22 duplicate target symbols accepted'; file=$APP; find='        if len(normalized) != len(tuple(self.symbols)):\n            raise ValueError("target symbols must be unique and nonblank")'; repl='        if False:\n            raise ValueError("target symbols must be unique and nonblank")'; test=$TEST; select='r93_spec_requires_unique_nonblank_symbols' },
    @{ name='R93-M23 target order not preserved'; file=$APP; find='                key=lambda item: self._target_rank(item.symbol),'; repl='                key=lambda item: item.symbol,'; test=$TEST; select='r93_targets_follow_operator_order' },
    @{ name='R93-M24 quote store opened writable'; file=$COMP; find='    store = MinuteQuoteStore(store_path, read_only=True)'; repl='    store = MinuteQuoteStore(store_path)'; test=$TEST; select='r93_10f_composition_opens_the_store_read_only' },
    @{ name='R93-M25 missing database silently created'; file=$COMP; find='    if not store_path.exists():\n        raise EvidenceStoreUnavailable('; repl='    if False:\n        raise EvidenceStoreUnavailable('; test=$TEST; select='r93_10d_composition_never_creates_the_database' },
    @{ name='R93-M26 health log written by the diagnostic'; file=$COMP; find='        path = Path(health_log)\n        try:'; repl='        path = Path(health_log)\n        path.parent.mkdir(parents=True, exist_ok=True)\n        path.write_text("", encoding="utf-8")\n        try:'; test=$TEST; select='r93_10c_health_log_is_never_created' },
    @{ name='R93-M27 recorder auto-started by the diagnostic'; file=$COMP; find='from us_quant.trading.application.market_evidence_readiness import ('; repl='from us_quant.trading.application.market_evidence_capture import (\n    MarketEvidenceCaptureApplication,\n)\nfrom us_quant.trading.application.market_evidence_readiness import ('; test=$TEST; select='r93_10e_readiness_layers_hold_no_write_or_trading_authority' },
    @{ name='R93-M28 configuration rewritten by the CLI'; file=$CLI; find='        config = load_config(args.config)'; repl='        _text = Path(args.config).read_text(encoding="utf-8")\n        Path(args.config).write_text(_text.replace("4002", "4003"), encoding="utf-8")\n        config = load_config(args.config)'; test=$TEST; select='r93_cli_end_to_end_over_a_real_capture_environment' },
    @{ name='R93-M29 socket probe forced off'; file=$CLI; find='        config.ibkr, checked=not args.skip_socket_probe'; repl='        config.ibkr, checked=False'; test=$TEST; select='r93_cli_probes_the_configured_socket' },
    @{ name='R93-M31 future health timestamp treated as fresh'; file=$APP; find='    if age < 0:
        return True'; repl='    if False:
        return True'; test=$TEST; select='r93_02g_future_health_timestamp_fails_closed' },
    @{ name='R93-M32 expected symbol not realtime ignored'; file=$APP; find='    if missing:
        blockers.append("MARKET_STREAM_SYMBOL_NOT_REALTIME")'; repl='    if False:
        blockers.append("MARKET_STREAM_SYMBOL_NOT_REALTIME")'; test=$TEST; select='r93_02e_expected_symbol_not_realtime_is_blocked' },
    @{ name='R93-M33 absent health log reported as healthy'; file=$COMP; find='    """The honest projection when no health line has been seen."""

    return MarketStreamProjection(
        observed=False,'; repl='    """The honest projection when no health line has been seen."""

    return MarketStreamProjection(
        observed=True,'; test=$TEST; select='r93_no_health_log_means_not_observed' },
    @{ name='R93-M34 recorder stalled status ignored'; file=$COMP; find='        stalled=str(payload.get("status") or "") == "CAPTURE_STALLED",'; repl='        stalled=False,'; test=$TEST; select='r93_stalled_recorder_status_is_reported' },
    @{ name='R93-M35 piped health lines ignored'; file=$CLI; find='    if payload is None:
        return stream'; repl='    return stream'; test=$TEST; select='r93_health_stdin_transport_reads_piped_lines' },
    @{ name='R93-M36 narrow recorder certifies unsubscribed targets'; file=$APP; find='    missing = tuple(\n        symbol for symbol in targets if symbol not in stream.realtime_symbols\n    )'; repl='    missing = tuple(\n        symbol for symbol in targets\n        if symbol in stream.expected_symbols\n        and symbol not in stream.realtime_symbols\n    )'; test=$TEST; select='r93_02h_narrow_recorder_cannot_certify_unsubscribed_targets' },
    @{ name='R93-M37 store blocker swallowed by connectivity failure'; file=$APP; find='        if evidence_unavailable_reason is not None:\n            blockers.append(evidence_unavailable_reason)'; repl='        if evidence_unavailable_reason is not None and not blockers:\n            blockers.append(evidence_unavailable_reason)'; test=$TEST; select='r93_02i_store_blocker_survives_connectivity_failure' },
    @{ name='R93-M30 readiness strategy parameters dropped'; file=$CLI; find='        if seed.strategy_id == READINESS_STRATEGY_ID:\n            return dict(seed.parameters)'; repl='        if seed.strategy_id == READINESS_STRATEGY_ID:\n            return dict(seed.parameters)\n        break'; test=$TEST; select='r93_cli_uses_shipped_intraday_parameters' }
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
    $anchor = $mutation.find.Replace('\n', "`n")
    $replacement = $mutation.repl.Replace('\n', "`n")
    $count = [regex]::Matches($original, [regex]::Escape($anchor)).Count
    if ($count -ne 1) { $errors += "$($mutation.name): anchors=$count"; continue }
    try {
        [IO.File]::WriteAllText($path, $original.Replace($anchor, $replacement), [Text.UTF8Encoding]::new($false))
        $output = & $py -m pytest (Join-Path $projectRoot $mutation.test) -q -k $mutation.select --tb=short 2>&1
        $code = $LASTEXITCODE
        if ($code -eq 1 -and ($output | Select-String -Pattern 'FAILED|AssertionError|DID NOT RAISE|IntegrityError|OperationalError' -Quiet) -and -not ($output | Select-String -Pattern 'no tests ran|ERROR collecting' -Quiet)) {
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
