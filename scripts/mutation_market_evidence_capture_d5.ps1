# Stage 6-D.5 semantic mutation gate: all named safety regressions must fail.
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$env:PYTHONPATH = (Join-Path $projectRoot 'src') + ';' + (Join-Path $projectRoot 'tests')
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTHONUTF8 = '1'
$py = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $py)) { $py = (Get-Command python -ErrorAction Stop).Source }
$mutations = @(
    @{ name='D5-01 overwrite existing captured minute'; file='src/us_quant/minute_data.py'; find='WHERE minute_quote.evidence_origin <> ''captured_stream'''; repl='WHERE 1=1'; test='tests/test_market_evidence_capture_d5.py'; select='capture_append_once_restart_and_next_minute or real_capture_replaces_preview' },
    @{ name='D5-02 restart clears durable fact'; file='src/us_quant/minute_data.py'; find='                connection.execute(`n                    """`n                    CREATE INDEX IF NOT EXISTS'; repl='                connection.execute("DELETE FROM minute_quote")`n                connection.execute(`n                    """`n                    CREATE INDEX IF NOT EXISTS'; test='tests/test_market_evidence_capture_d5.py'; select='capture_append_once_restart_and_next_minute' },
    @{ name='D5-03 minute sampling keeps seconds'; file='src/us_quant/minute_data.py'; find='return value.replace(second=0, microsecond=0).isoformat()'; repl='return value.isoformat()'; test='tests/test_market_evidence_capture_d5.py'; select='capture_append_once_restart_and_next_minute' },
    @{ name='D5-04 symbol independence removed'; file='src/us_quant/minute_data.py'; find='UNIQUE(symbol, minute, provider)'; repl='UNIQUE(minute, provider)'; test='tests/test_market_evidence_capture_d5.py'; select='symbols_and_providers_remain_independent' },
    @{ name='D5-05 provider independence removed'; file='src/us_quant/minute_data.py'; find='UNIQUE(symbol, minute, provider)'; repl='UNIQUE(symbol, minute)'; test='tests/test_market_evidence_capture_d5.py'; select='symbols_and_providers_remain_independent' },
    @{ name='D5-06 stale observations filtered'; file='src/us_quant/minute_data.py'; find='for quote in snapshot.quotes:'; repl='for quote in snapshot.quotes:`n        if quote.stale:`n            continue'; test='tests/test_market_evidence_capture_d5.py'; select='persists_bad_rows' },
    @{ name='D5-07 delayed observations filtered'; file='src/us_quant/minute_data.py'; find='for quote in snapshot.quotes:'; repl='for quote in snapshot.quotes:`n        if quote.mode is not MarketDataMode.REALTIME:`n            continue'; test='tests/test_market_evidence_capture_d5.py'; select='persists_bad_rows' },
    @{ name='D5-08 missing bid or ask filtered'; file='src/us_quant/minute_data.py'; find='for quote in snapshot.quotes:'; repl='for quote in snapshot.quotes:`n        if quote.bid is None or quote.ask is None:`n            continue'; test='tests/test_market_evidence_capture_d5.py'; select='persists_bad_rows' },
    @{ name='D5-09 production origin is not captured_stream'; file='src/us_quant/minute_data.py'; find='evidence_origin="captured_stream",'; repl='evidence_origin="synthetic_preview",'; test='tests/test_market_evidence_capture_d5.py'; select='persists_bad_rows' },
    @{ name='D5-10 preview origin admitted'; file='src/us_quant/trading/application/market_evidence_readiness.py'; find='if row.evidence_origin == "captured_stream"'; repl='if True'; test='tests/test_market_evidence_capture_d5.py'; select='rejects_identity_mismatch_and_preview' },
    @{ name='D5-11 source identity mismatch accepted'; file='src/us_quant/trading/application/market_evidence_capture.py'; find='if snapshot.source_id != self.source_id:'; repl='if False:'; test='tests/test_market_evidence_capture_d5.py'; select='rejects_identity_mismatch_and_preview' },
    @{ name='D5-12 provider identity mismatch accepted'; file='src/us_quant/trading/application/market_evidence_capture.py'; find='if quote.source_id != self.source_id or quote.source_label != self.provider:'; repl='if False:'; test='tests/test_market_evidence_capture_d5.py'; select='quote_provider_identity_mismatch' },
    @{ name='D5-13 out-of-campaign symbol accepted'; file='src/us_quant/trading/application/market_evidence_capture.py'; find='if quote.symbol != quote.symbol.strip().upper() or quote.symbol not in allowed:'; repl='if False:'; test='tests/test_market_evidence_capture_d5.py'; select='rejects_identity_mismatch_and_preview' },
    @{ name='D5-14 duplicate count omitted'; file='src/us_quant/minute_data.py'; find='duplicate_rows_ignored=len(rows) - len(inserted_rows),'; repl='duplicate_rows_ignored=0,'; test='tests/test_market_evidence_capture_d5.py'; select='capture_append_once_restart_and_next_minute' },
    @{ name='D5-15 readiness combines providers'; file='src/us_quant/trading/application/market_evidence_readiness.py'; find='if len(providers) > 1:'; repl='if False:'; test='tests/test_market_evidence_capture_d5.py'; select='provider_mixing_rejected' },
    @{ name='D5-16 24 sessions marked READY'; file='src/us_quant/trading/application/market_evidence_readiness.py'; find='ready = review_ready >= required_sessions'; repl='ready = review_ready >= required_sessions - 1'; test='tests/test_market_evidence_capture_d5.py'; select='exact_25_qualified_sessions_ready_and_24_collecting' },
    @{ name='D5-17 completeness threshold ignored'; file='src/us_quant/trading/domain/market_evidence_quality.py'; find='MINIMUM_COMPLETENESS = Decimal("0.98")'; repl='MINIMUM_COMPLETENESS = Decimal("0")'; test='tests/test_market_evidence_capture_d5.py'; select='eight_disjoint_missing_minutes_fail_completeness_only' },
    @{ name='D5-18 consecutive gaps accepted'; file='src/us_quant/trading/domain/market_evidence_quality.py'; find='MAXIMUM_CONSECUTIVE_MISSING = 2'; repl='MAXIMUM_CONSECUTIVE_MISSING = 8'; test='tests/test_market_evidence_capture_d5.py'; select='bad_completeness_gap_or_age' },
    @{ name='D5-19 excessive source age accepted'; file='src/us_quant/trading/domain/market_evidence_quality.py'; find='MAXIMUM_P95_SOURCE_AGE_SECONDS = Decimal("5")'; repl='MAXIMUM_P95_SOURCE_AGE_SECONDS = Decimal("500")'; test='tests/test_market_evidence_capture_d5.py'; select='bad_completeness_gap_or_age' },
    @{ name='D5-20 UTC time replaces New York session window'; file='src/us_quant/trading/domain/market_evidence_quality.py'; find='def minute_is_in_evaluation_window(`n    value: str,`n    session_dates: tuple[str, ...] | None = None,`n) -> bool:`n    eastern = parse_minute(value).astimezone(NEW_YORK)'; repl='def minute_is_in_evaluation_window(`n    value: str,`n    session_dates: tuple[str, ...] | None = None,`n) -> bool:`n    eastern = parse_minute(value).astimezone(timezone.utc)'; test='tests/test_market_evidence_capture_d5.py'; select='new_york_dst_and_weekends' },
    @{ name='D5-21 weekends counted'; file='src/us_quant/trading/domain/market_evidence_quality.py'; find='if eastern.weekday() >= 5:'; repl='if False:'; test='tests/test_market_evidence_capture_d5.py'; select='new_york_dst_and_weekends' },
    @{ name='D5-22 stale rows count as usable'; file='src/us_quant/trading/domain/market_evidence_quality.py'; find='def _quality_usable(row: MinuteEvidenceRecord) -> bool:`n    return (`n        row.realtime_ready`n        and not row.stale'; repl='def _quality_usable(row: MinuteEvidenceRecord) -> bool:`n    return (`n        row.realtime_ready'; test='tests/test_minute_evidence_quality_characterization.py'; select='one_stale_minute' },
    @{ name='D5-23 missing bid ignored'; file='src/us_quant/trading/domain/market_evidence_quality.py'; find='and row.bid is not None`n        and row.ask is not None`n        and row.bid > 0'; repl='and row.ask is not None`n        and row.bid > 0'; test='tests/test_market_evidence_capture_d5.py'; select='missing_quote_side' },
    @{ name='D5-24 missing ask ignored'; file='src/us_quant/trading/domain/market_evidence_quality.py'; find='and row.ask is not None`n        and row.bid > 0`n        and row.ask >= row.bid'; repl='and row.bid > 0`n        and row.ask >= row.bid'; test='tests/test_market_evidence_capture_d5.py'; select='missing_quote_side' },
    @{ name='D5-25 CLI gains forbidden trading authority'; file='src/us_quant/market_evidence_capture.py'; find='from us_quant.config import load_config'; repl='from us_quant.trading.application.execution import ExecutionApplication`nfrom us_quant.config import load_config'; test='tests/test_market_evidence_capture_d5.py'; select='capture_cli_has_no_trading_or_qt_authority' },
    @{ name='D5-26 generic snapshot overwrites captured evidence'; file='src/us_quant/minute_data.py'; find='WHERE minute_quote.evidence_origin <> ''captured_stream'''; repl='WHERE 1=1'; test='tests/test_market_evidence_capture_d5.py'; select='generic_snapshot_cannot_overwrite_captured_minute' },
    @{ name='D5-27 readiness loads every provider'; file='src/us_quant/trading/application/market_evidence_readiness.py'; find='            provider=provider,`n            usable_only=False,'; repl='            usable_only=False,'; test='tests/test_market_evidence_capture_d5.py'; select='readiness_application_scopes_rows_to_requested_provider' },
    @{ name='D5-28 partial realtime symbols report healthy'; file='src/us_quant/trading/application/market_evidence_capture.py'; find='set(realtime_symbols) == set(self.expected_symbols)'; repl='bool(realtime_symbols)'; test='tests/test_market_evidence_capture_d5.py'; select='capture_health_requires_every_expected_symbol_realtime' },
    @{ name='D5-29 duplicate snapshot key silently admitted'; file='src/us_quant/minute_data.py'; find='        if key in seen_keys:`n            raise ValueError('; repl='        if False:`n            raise ValueError('; test='tests/test_market_evidence_capture_d5.py'; select='duplicate_snapshot_key_is_rejected_before_durable_write' },
    @{ name='D5-30 desktop cache claims durable origin'; file='src/us_quant/desktop.py'; find='evidence_origin="live_stream_cache"'; repl='evidence_origin="captured_stream"'; test='tests/test_market_evidence_capture_d5.py'; select='desktop_minute_recorder_uses_mutable_live_cache_origin' },
    @{ name='D5-31 stale evidence uses last tick minute'; file='src/us_quant/minute_data.py'; find='            snapshot,`n            symbols=symbols,`n            evidence_origin="captured_stream",`n            use_snapshot_observation=True,'; repl='            snapshot,`n            symbols=symbols,`n            evidence_origin="captured_stream",`n            use_snapshot_observation=False,'; test='tests/test_market_evidence_capture_d5.py'; select='stale_quote_is_bucketed_by_observation_minute' },
    @{ name='D5-32 push source stale snapshots skipped'; file='src/us_quant/market_evidence_capture.py'; find='            capture.capture(snapshot)'; repl='            if args.source not in PUSH_LISTENER_SOURCES:`n                capture.capture(snapshot)'; test='tests/test_market_evidence_capture_d5.py'; select='capture_cli_polls_push_sources_for_stale_transitions' },
    @{ name='D5-33 mutable cache uses observation minute'; file='src/us_quant/minute_data.py'; find='            evidence_origin=evidence_origin,`n            use_snapshot_observation=False,'; repl='            evidence_origin=evidence_origin,`n            use_snapshot_observation=True,'; test='tests/test_market_evidence_capture_d5.py'; select='generic_live_cache_keeps_quote_minute' },
    @{ name='D5-34 legacy captured rows remain trusted'; file='src/us_quant/minute_data.py'; find='        UPDATE minute_quote`n        SET evidence_origin = ''live_stream_cache''`n        WHERE evidence_origin = ''captured_stream'''; repl='        SELECT 1'; test='tests/test_minute_data_migration.py'; select='legacy_captured_rows_are_demoted_from_durable_evidence_once' },
    @{ name='D5-35 generic snapshot can claim durable origin'; file='src/us_quant/minute_data.py'; find='            "live_stream_cache",`n            "synthetic_preview",'; repl='            "captured_stream",`n            "live_stream_cache",`n            "synthetic_preview",'; test='tests/test_market_evidence_capture_d5.py'; select='generic_snapshot_cannot_claim_durable_origin' },
    @{ name='D5-36 push listener writes synchronously'; file='src/us_quant/market_evidence_capture.py'; find='market_data.prepare(request)'; repl='market_data.prepare(request, listener=capture.capture)'; test='tests/test_market_evidence_capture_d5.py'; select='capture_cli_keeps_durable_writes_off_push_listener' }
)
$baseline = & $py -m pytest (Join-Path $projectRoot 'tests/test_market_evidence_capture_d5.py') (Join-Path $projectRoot 'tests/test_minute_evidence_quality_characterization.py') -q 2>&1
if ($LASTEXITCODE -ne 0) { Write-Host ($baseline -join "`n"); throw 'Baseline is not GREEN' }
$survivors = @()
$errors = @()
foreach ($mutation in $mutations) {
    $path = [IO.Path]::GetFullPath((Join-Path $projectRoot $mutation.file))
    if (-not $path.StartsWith([IO.Path]::GetFullPath($projectRoot) + [IO.Path]::DirectorySeparatorChar)) { throw 'Mutation path escaped workspace' }
    $original = [IO.File]::ReadAllText($path).Replace("`r`n", "`n")
    $anchor = $mutation.find.Replace("`r`n", "`n").Replace('`n', "`n")
    $replacement = $mutation.repl.Replace("`r`n", "`n").Replace('`n', "`n")
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
