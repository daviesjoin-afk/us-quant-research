# Stage 5-A deterministic portfolio allocator mutation gate.
# Every single-point mutant must fail its named pytest assertion.

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$env:PYTHONPATH = Join-Path $projectRoot "src"
$env:PYTHONUTF8 = "1"
$py = Join-Path $projectRoot ".venv314\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $py)) { $py = (Get-Command python -ErrorAction Stop).Source }

$domain = Join-Path $projectRoot "src\us_quant\trading\domain\portfolio.py"
$application = Join-Path $projectRoot "src\us_quant\trading\application\portfolio.py"
$allocatorTests = Join-Path $projectRoot "tests\test_capital_allocator.py"
$architectureTests = Join-Path $projectRoot "tests\test_portfolio_architecture.py"

function Get-Text([string]$path) { [System.IO.File]::ReadAllText($path) }
function Set-Text([string]$path, [string]$text) {
    [System.IO.File]::WriteAllText($path, $text, (New-Object System.Text.UTF8Encoding($false)))
}

$mutations = @(
    @{ name='M1 disabled/unallocated strategy is allowed'; file=$application; find='if not allocation\.enabled:'; repl='if False:'; test=$allocatorTests; select='governed_but_disabled_strategy_is_not_allocated' },
    @{ name='M2 zero strategy allocation becomes valid'; file=$application; find='ceiling = min\(\s+allocation\.max_capital,\s+policy\.total_capital_limit \* allocation\.capital_weight,\s+allocation\.max_gross_exposure,\s+snapshot\.equity \* policy\.max_strategy_concentration,\s+\)'; repl='ceiling = policy.total_capital_limit'; test=$allocatorTests; select='zero_allocation_is_rejected' },
    @{ name='M4 portfolio capital cap is ignored'; file=$application; find='if \(\s+projected_gross > min\(policy\.total_capital_limit, policy\.max_gross_exposure\)\s+or abs\(projected_net\) > policy\.max_net_exposure\s+\):'; repl='if False:'; test=$allocatorTests; select='total_capital_limit_rejects' },
    @{ name='M3 strategy allocation cap is ignored'; file=$application; find='if projected > ceiling:'; repl='if False:'; test=$allocatorTests; select='strategy_cap_is_checked' },
    @{ name='M5 symbol concentration cap is ignored'; file=$application; find='if \(\s+projected_symbol > policy\.max_single_position_notional\s+or projected_symbol > snapshot\.equity \* policy\.max_symbol_concentration\s+\):'; repl='if False:'; test=$allocatorTests; select='symbol_concentration_is_portfolio_wide' },
    @{ name='M6 portfolio position cap is ignored'; file=$application; find='if len\(active_positions\) > policy\.max_positions:'; repl='if False:'; test=$allocatorTests; select='position_and_open_order_limits_count_at_portfolio_symbol_level' },
    @{ name='M7 opposite strategy intents do not net'; file=$application; find='net_quantity = sum\(\s+item\.requested_quantity\s+if item\.side is PortfolioSide\.BUY\s+else\s+-item\.requested_quantity\s+for item in items\s+\)'; repl='net_quantity = sum(item.requested_quantity for item in items)'; test=$allocatorTests; select='opposite_intents_net_and_keep_strategy_attribution' },
    @{ name='M8 fully offset intents create a non-zero action'; file=$application; find='net_quantity = sum\(item\.signed_requested_quantity for item in attributions\)'; repl='net_quantity = sum(abs(item.signed_requested_quantity) for item in attributions)'; test=$allocatorTests; select='fully_offset_intents_approve_without_creating_an_action' },
    @{ name='M10 duplicate proposal identities are accepted'; file=$application; find='if any\(count > 1 for count in proposal_counts\.values\(\)\):'; repl='if False:'; test=$allocatorTests; select='duplicate_proposal_identity_and_reference_conflict_reject' },
    @{ name='M9 input arrival order changes attribution order'; file=$application; find='return \(\s+item\.strategy_version_id,\s+item\.proposal_id,\s+item\.side\.value,\s+item\.requested_quantity,\s+item\.reference_price,\s+\)'; repl='return (item.symbol,)'; test=$allocatorTests; select='intent_order_does_not_change_decisions_or_attribution' },
    @{ name='M11 allocator refers to forbidden OrderIntent'; file=$application; find='class CapitalAllocator:'; repl="if False:`n    OrderIntent()`n`n`nclass CapitalAllocator:"; test=$architectureTests; select='portfolio_application_cannot_reach_execution_or_broker_authorities' },
    @{ name='M12 unknown strategy receives implicit capital'; file=$domain; find='return next\(\s+\(\s+item\s+for item in self\.allocations\s+if item\.strategy_version_id == strategy_version_id\s+\),\s+None,\s+\)'; repl='return next((item for item in self.allocations if item.strategy_version_id == strategy_version_id), PortfolioStrategyAllocation(strategy_version_id, Decimal("1"), self.total_capital_limit, self.max_gross_exposure, True))'; test=$allocatorTests; select='unconfigured_default_policy_rejects_and_unallocated_strategy_cannot_trade' },
    @{ name='M13 symbol canonicalization is removed'; file=$domain; find='return value\.strip\(\)\.upper\(\)'; repl='return value'; test=$allocatorTests; select='mixed_case_opposite_intents_net_to_one_canonical_action' }
)

$results = @()
foreach ($mutation in $mutations) {
    $path = $mutation.file
    $original = Get-Text $path
    $regex = [regex]::new($mutation.find)
    $matches = $regex.Matches($original)
    if ($matches.Count -ne 1) {
        $results += [pscustomobject]@{ Mutation=$mutation.name; Caught=$false; Detail="expected 1 anchor, found $($matches.Count)" }
        continue
    }
    $backup = "$path.stage5a-mutation-backup"
    Copy-Item -LiteralPath $path -Destination $backup -Force
    try {
        Set-Text $path ($regex.Replace($original, $mutation.repl, 1))
        $syntax = & $py -c "import ast,sys; ast.parse(open(sys.argv[1],encoding='utf-8').read())" $path 2>&1
        if ($LASTEXITCODE -ne 0) {
            $caught = $false
            $detail = "invalid syntax: $($syntax -join ' ')"
        }
        else {
            $output = & $py -m pytest $mutation.test -q -k $mutation.select 2>&1
            $exitCode = $LASTEXITCODE
            $countLine = $output | Select-String -Pattern '\d+ failed' | Select-Object -First 1
            $assertionFailure = $output | Select-String -Pattern 'AssertionError|assert .* ==|assert .* is|DID NOT RAISE' -Quiet
            $caught = ($exitCode -eq 1 -and $countLine -and $assertionFailure)
            $detail = if ($caught) { $countLine.Line.Trim() } else { "exit=$exitCode; $($output -join ' ')" }
        }
    }
    catch {
        $caught = $false
        $detail = $_.Exception.Message
    }
    finally {
        Copy-Item -LiteralPath $backup -Destination $path -Force
        Remove-Item -LiteralPath $backup -Force
    }
    $results += [pscustomobject]@{ Mutation=$mutation.name; Caught=[bool]$caught; Detail=$detail }
    Write-Host ("{0,-58} RED={1}  {2}" -f $mutation.name, $caught, $detail)
}

$uncaught = @($results | Where-Object { $_.Caught -ne $true })
Write-Host "mutations: $($results.Count)   red: $(@($results | Where-Object { $_.Caught -eq $true }).Count)   not caught: $($uncaught.Count)"
foreach ($row in $uncaught) { Write-Host "  SURVIVED / HARNESS-ERROR: $($row.Mutation) -> $($row.Detail)" }
if ($uncaught.Count -gt 0) { exit 1 }
exit 0
