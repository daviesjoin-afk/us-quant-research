[CmdletBinding(PositionalBinding = $false)]
param(
    [string]$From = "main",
    [string]$To = "HEAD",
    [ValidateSet("delegate", "provider")]
    [string]$Mode = "delegate",
    [ValidateSet("text", "json", "sarif")]
    [string]$Format = "json",
    [string]$OutputDir = ".ocr-output",
    [string]$Rule = "",
    [string]$Background = "",
    [string]$BackgroundFile = "",
    [switch]$Preview
)

# Local OpenCodeReview wrapper.
#
# This file is a **wrapper and nothing else**.  It selects a range, calls the
# ``ocr`` binary, and writes what OCR produced into ``.ocr-output/``.  It makes
# no judgement about whether a finding is real, whether a review should block a
# merge, or whether the reviewed code is safe: it prints a summary of what was
# written and exits with the exit code the wrapped command returned.  A wrapper
# that decided "looks fine" would be a second source of truth for exactly the
# question OCR exists to answer independently.
#
# What this repository's review loop is, and what each layer is responsible
# for, is documented in ``docs/CODE_REVIEW.md``.  The short version: the
# deterministic gates (pytest, architecture guards, mutation tests, doctor,
# compileall, the offscreen desktop self-test, Python 3.14 CI) are the source of
# truth, and OpenCodeReview is an **advisory** independent reviewer on top.
#
# Two modes, because OCR can be driven two ways:
#
#   -Mode delegate   (default)
#       OCR does file selection and rule resolution -- the parts that must be
#       deterministic -- and prints a review spec.  The **host agent** supplies
#       the LLM reasoning.  No OCR provider, API key or token is configured or
#       needed, which is why this is the default: it keeps credentials out of
#       the repository entirely.
#
#         ocr delegate preview   -> .ocr-output/<stamp>/preview.json
#         ocr delegate rule ...  -> .ocr-output/<stamp>/rules-NN.json
#
#       Pass those files, plus ``git diff <merge_base>..<to>``, to the host
#       agent.  ``preview.json`` names the reviewable files and the merge base
#       to diff from; ``rules-NN.json`` carries the resolved rule per file
#       group, with the built-in language rules already merged in.
#
#   -Mode provider
#       OCR calls its own configured LLM and writes a finished review.
#       Requires a prior ``ocr config provider`` / ``ocr config model``.  That
#       configuration lives in the user's ``~/.opencodereview`` directory and
#       must never be committed here.
#
# ``-Format`` applies to ``-Mode provider``.  In delegate mode OCR is always
# asked for JSON: the output is consumed by a machine, and ``ocr delegate``
# does not support ``sarif`` at all.
#
# ``-Preview`` passes ``--preview`` to ``ocr review``, which prints the file
# selection without spending a token.  It is the cheap way to check that a
# change to ``.opencodereview/rule.json`` selects what you meant it to -- in
# particular, the ``include`` entry below, without which every
# ``tests/test_*.py`` file is dropped by OCR's built-in test-file exclusion.
#
# ``-Rule`` overrides the project rule file for one run (``ocr --rule``), which
# is how a rule change is evaluated before it is committed.
#
# Examples:
#
#     .\scripts\review.ps1
#     .\scripts\review.ps1 -From main -To chore/open-code-review-integration
#     .\scripts\review.ps1 -Preview -Mode provider
#     .\scripts\review.ps1 -Mode provider -Format sarif
#
# One calling convention, the same one ``verify.ps1`` documents: invoke the
# script **directly** with a real array/string, not through ``pwsh -File``.

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
Push-Location $projectRoot

# The context handed to the reviewer on every run.  It states what the project
# is and which properties a reviewer should already assume are *policy* -- so
# that findings arrive as defects against a known architecture rather than as
# generic advice to adopt one.
$projectContext = @(
    "Local-first US equities quantitative trading platform."
    "Architecture emphasizes deterministic risk,"
    "single ownership, explicit state machines,"
    "Paper/Live path parity, and auditable execution."
    ""
    "Deterministic gates (pytest, architecture guards, mutation tests, doctor,"
    "compileall, the offscreen desktop self-test, and Python 3.14 CI) remain the"
    "source of truth for correctness. This review is advisory."
) -join "`n"

function Stop-WithMessage {
    param(
        [Parameter(Mandatory = $true)][string]$Message,
        [int]$Code = 2
    )
    Write-Host ""
    Write-Host $Message -ForegroundColor Red
    exit $Code
}

try {
    # -- preconditions, each failing with the fix rather than a stack trace ---

    if (-not (Get-Command ocr -ErrorAction SilentlyContinue)) {
        Stop-WithMessage @"
OpenCodeReview is not installed, so there is nothing to wrap.

    npm install -g @alibaba-group/open-code-review
    ocr --help

Then re-run this script. Nothing else in the repository requires it: the
deterministic gates do not depend on OCR, and a missing reviewer must not be
able to look like a passing review.
"@
    }

    $gitRaw = & git --version 2>$null
    if ($LASTEXITCODE -ne 0 -or -not $gitRaw) {
        Stop-WithMessage "git is not available on PATH; OpenCodeReview needs it for diff generation."
    }
    $gitVersionText = ($gitRaw -replace '^git version\s+', '') -replace '\.windows\.\d+$', ''
    try {
        $gitVersion = [version]$gitVersionText
    }
    catch {
        # An unparseable but present git is not worth failing over; the version
        # that matters is enforced by OCR itself.
        $gitVersion = $null
    }
    if ($gitVersion -and $gitVersion -lt [version]'2.41') {
        Stop-WithMessage "OpenCodeReview requires Git >= 2.41; found $gitVersionText."
    }

    foreach ($ref in @($From, $To)) {
        & git rev-parse --verify --quiet "$ref^{commit}" 2>$null | Out-Null
        if ($LASTEXITCODE -ne 0) {
            Stop-WithMessage "'$ref' does not resolve to a commit in this repository. Pass -From/-To as refs that exist locally (e.g. 'main', 'HEAD', a branch or a SHA)."
        }
    }

    $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $runDir = Join-Path (Join-Path $projectRoot $OutputDir) $stamp
    New-Item -ItemType Directory -Force -Path $runDir | Out-Null

    $contextArgs = @()
    if ($BackgroundFile) {
        if (-not (Test-Path -LiteralPath $BackgroundFile)) {
            Stop-WithMessage "-BackgroundFile '$BackgroundFile' does not exist."
        }
        $contextArgs += @("--background-file", $BackgroundFile)
    }
    elseif ($Background) {
        $contextArgs += @("--background", $Background)
    }
    else {
        $contextArgs += @("--background", $projectContext)
    }

    $ruleArgs = @()
    if ($Rule) {
        if (-not (Test-Path -LiteralPath $Rule)) {
            Stop-WithMessage "-Rule '$Rule' does not exist."
        }
        $ruleArgs += @("--rule", $Rule)
    }

    Write-Host ""
    Write-Host "== OpenCodeReview ($Mode mode)" -ForegroundColor Cyan
    Write-Host "   range : $From -> $To"
    Write-Host "   output: $runDir"

    if ($Mode -eq "delegate") {
        # -- delegation: OCR resolves files and rules, the host agent reasons --

        $previewFile = Join-Path $runDir "preview.json"
        $delegatePreviewArgs = @(
            "delegate", "preview", "--from", $From, "--to", $To, "--format", "json"
        ) + $contextArgs + $ruleArgs

        & ocr @delegatePreviewArgs | Set-Content -LiteralPath $previewFile -Encoding utf8
        $previewCode = $LASTEXITCODE
        if ($previewCode -ne 0) {
            Stop-WithMessage "ocr delegate preview failed (exit $previewCode)." -Code $previewCode
        }

        # Named ``$previewSpec``, never ``$preview``: PowerShell variable names are
        # case-insensitive, so a local ``$preview`` *is* the ``[switch]$Preview``
        # parameter.  Assigning the parsed object to it fails with a confusing
        # "cannot convert ... to SwitchParameter" from a line that looks innocent.
        $previewSpec = Get-Content -LiteralPath $previewFile -Raw | ConvertFrom-Json
        $paths = @($previewSpec.reviewable_files | ForEach-Object { $_.path })

        Write-Host "   files : $($paths.Count) reviewable of $($previewSpec.total_files) changed"
        Write-Host "   merge base: $($previewSpec.merge_base)"

        if ($paths.Count -eq 0) {
            Write-Host ""
            Write-Host "Nothing reviewable in this range. preview.json still records the selection." -ForegroundColor Yellow
            exit 0
        }

        # Files that resolve to the same rule are grouped by OCR, and the group
        # list is what the host agent reads.  One invocation per batch keeps the
        # command line well inside the Windows limit on a large changeset.
        $maxBatchChars = 6000
        $batches = New-Object System.Collections.Generic.List[object]
        $currentBatch = New-Object System.Collections.Generic.List[string]
        $currentLength = 0
        foreach ($path in $paths) {
            if ($currentBatch.Count -gt 0 -and ($currentLength + $path.Length) -gt $maxBatchChars) {
                $batches.Add($currentBatch.ToArray())
                $currentBatch = New-Object System.Collections.Generic.List[string]
                $currentLength = 0
            }
            $currentBatch.Add($path)
            $currentLength += $path.Length + 1
        }
        if ($currentBatch.Count -gt 0) {
            $batches.Add($currentBatch.ToArray())
        }

        $rulesFiles = @()
        for ($index = 0; $index -lt $batches.Count; $index++) {
            $rulesFile = Join-Path $runDir ("rules-{0:d2}.json" -f ($index + 1))
            $delegateRuleArgs = @(
                "delegate", "rule", "--format", "json"
            ) + $contextArgs + $ruleArgs + $batches[$index]

            & ocr @delegateRuleArgs | Set-Content -LiteralPath $rulesFile -Encoding utf8
            $ruleCode = $LASTEXITCODE
            if ($ruleCode -ne 0) {
                Stop-WithMessage "ocr delegate rule failed (exit $ruleCode)." -Code $ruleCode
            }
            $rulesFiles += $rulesFile
        }

        Write-Host ""
        Write-Host "Review spec written. Hand these to the host agent:" -ForegroundColor Green
        Write-Host "   $previewFile"
        foreach ($rulesFile in $rulesFiles) {
            Write-Host "   $rulesFile"
        }
        Write-Host ""
        Write-Host "   git diff $($previewSpec.merge_base)..$To"
        Write-Host ""
        Write-Host "Findings are advisory. Triage each one against the code before acting:" -ForegroundColor Yellow
        Write-Host "docs/CODE_REVIEW.md describes the loop, and the paths that are review-only."
        exit 0
    }

    # -- provider mode: OCR runs the review with its own configured LLM -------

    $extension = switch ($Format) {
        "json" { "json" }
        "sarif" { "sarif" }
        default { "txt" }
    }
    $resultFile = Join-Path $runDir "review.$extension"

    $reviewArgs = @(
        "review", "--from", $From, "--to", $To,
        "--format", $Format, "--output", $resultFile, "--audience", "agent"
    ) + $contextArgs + $ruleArgs
    if ($Preview) {
        $reviewArgs += "--preview"
    }

    & ocr @reviewArgs
    $reviewCode = $LASTEXITCODE

    Write-Host ""
    if ($reviewCode -eq 0) {
        Write-Host "Review written to $resultFile" -ForegroundColor Green
        Write-Host "Findings are advisory; triage them against docs/CODE_REVIEW.md." -ForegroundColor Yellow
    }
    else {
        Write-Host "ocr review exited $reviewCode. See docs/CODE_REVIEW.md for setup" -ForegroundColor Red
        Write-Host "(a provider must be configured with 'ocr config provider' / 'ocr config model')."
    }
    exit $reviewCode
}
finally {
    Pop-Location
}
