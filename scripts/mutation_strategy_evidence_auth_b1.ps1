# Stage 6-B1 authenticated research evidence mutation harness.
#
# Each mutation breaks one security-relevant branch in production code and
# asserts that a specific test turns RED.  A survivor means the suite does not
# actually pin the property it claims to.
#
# Note on quoting: single-quoted PowerShell strings do NOT expand `n, so every
# multi-line replacement below is written with double quotes and `" escapes.

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$env:PYTHONPATH = Join-Path $projectRoot "src"
$env:PYTHONUTF8 = "1"
$env:QT_QPA_PLATFORM = "offscreen"
$py = $env:US_QUANT_PYTHON
if (-not $py) { $py = Join-Path $projectRoot ".venv\Scripts\python.exe" }
if (-not (Test-Path -LiteralPath $py)) { $py = (Get-Command python -ErrorAction Stop).Source }

$domain = Join-Path $projectRoot "src\us_quant\trading\domain\evidence_auth.py"
$application = Join-Path $projectRoot "src\us_quant\trading\application\evidence_authentication.py"
$repository = Join-Path $projectRoot "src\us_quant\trading\adapters\sqlite\evidence_authentication_repository.py"
$signature = Join-Path $projectRoot "src\us_quant\trading\adapters\evidence_signature.py"
$composition = Join-Path $projectRoot "src\us_quant\trading\composition\evidence_authentication.py"
$sealing = Join-Path $projectRoot "src\us_quant\research_evidence_sealing.py"

$tApplication = Join-Path $projectRoot "tests\test_evidence_authentication_application.py"
$tRepository = Join-Path $projectRoot "tests\test_evidence_authentication_repository.py"
$tDomain = Join-Path $projectRoot "tests\test_evidence_auth_domain.py"
$tSealing = Join-Path $projectRoot "tests\test_research_evidence_sealing.py"
$tArchitecture = Join-Path $projectRoot "tests\test_evidence_auth_architecture.py"

$mutations = @(
    # -- artifact / seal binding -----------------------------------------
    @{ name='M01 artifact digest binding removed'; file=$domain; find='if seal\.artifact_payload_sha256 != artifact_digest:'; repl='if False:'; tests=@($tApplication); select='test_artifact_payload_change_fails_closed' },
    @{ name='M02 observed tamper accepted'; file=$domain; find='if seal\.artifact_payload_sha256 != artifact_digest:'; repl='if False:'; tests=@($tApplication); select='test_observed_change_fails_closed' },
    @{ name='M03 self-consistent passed forgery accepted'; file=$domain; find='if seal\.artifact_payload_sha256 != artifact_digest:'; repl='if False:'; tests=@($tApplication); select='test_passed_change_with_consistent_aggregates_fails_closed' },
    @{ name='M04 generated_at tamper accepted'; file=$domain; find='if seal\.artifact_payload_sha256 != artifact_digest:'; repl='if False:'; tests=@($tApplication); select='test_generated_at_change_fails_closed' },
    @{ name='M05 review run binding removed'; file=$domain; find='if seal\.review_run_id != evidence\.review_run_id:\s*\n\s*blockers\.add\(EvidenceAuthenticationBlocker\.REVIEW_RUN_MISMATCH\)'; repl="if False:`n            blockers.add(EvidenceAuthenticationBlocker.REVIEW_RUN_MISMATCH)"; tests=@($tApplication); select='test_seal_review_run_change_fails_closed' },
    @{ name='M06 seal-vs-evidence parameter binding removed'; file=$domain; find='if seal\.parameter_hash != identity\.parameter_hash:'; repl='if False:'; tests=@($tApplication); select='test_parameter_hash_change_fails_closed' },
    @{ name='M07 seal-vs-evidence data binding removed'; file=$domain; find='if seal\.data_hash != identity\.data_hash:'; repl='if False:'; tests=@($tApplication); select='test_data_hash_change_fails_closed' },
    @{ name='M08 code_hash not bound to the governed version'; file=$domain; find='if seal\.code_hash != version\.code_hash:'; repl='if False:'; tests=@($tApplication); select='test_code_hash_change_fails_closed' },
    @{ name='M09 universe_hash not bound to the governed version'; file=$domain; find='if seal\.universe_hash != version\.universe_hash:'; repl='if False:'; tests=@($tApplication); select='test_universe_hash_change_fails_closed' },
    @{ name='M10 version parameter binding removed'; file=$domain; find='if seal\.parameter_hash != version\.parameter_hash:'; repl='if False:'; tests=@($tApplication); select='test_version_parameter_hash_change_fails_closed' },
    @{ name='M11 version semver binding removed'; file=$domain; find='or seal\.strategy_semver != version\.semver'; repl='or False'; tests=@($tApplication); select='test_version_semver_change_fails_closed' },

    # -- what the signature covers ---------------------------------------
    @{ name='M12 code_hash dropped from the signed material'; file=$domain; find='"code_hash": seal\.code_hash,'; repl=''; tests=@($tApplication); select='tampering_with_any_signed_seal_field_fails_closed and code_hash' },
    @{ name='M13 universe_hash dropped from the signed material'; file=$domain; find='"universe_hash": seal\.universe_hash,'; repl=''; tests=@($tApplication); select='tampering_with_any_signed_seal_field_fails_closed and universe_hash' },
    @{ name='M14 artifact digest dropped from the signed material'; file=$domain; find='"artifact_payload_sha256": seal\.artifact_payload_sha256,'; repl=''; tests=@($tApplication); select='tampering_with_any_signed_seal_field_fails_closed and artifact_payload_sha256' },
    @{ name='M15 seal schema version check removed'; file=$domain; find='if schema_version != EVIDENCE_SEAL_SCHEMA_VERSION:'; repl='if False:'; tests=@($tApplication); select='test_seal_with_wrong_schema_version_fails_closed' },
    @{ name='M16 authentication identity ignores verdict and blockers'; file=$domain; find='"verdict": verdict\.value,\s*\n\s*"blockers": sorted\(\{item\.value for item in blockers\}\),\s*\n\s*"policy_version"'; repl='"policy_version"'; tests=@($tApplication); select='test_revocation_produces_a_distinct_identity' },
    @{ name='M17 authenticated evidence token guard removed'; file=$domain; find='if _authenticator_token is not _AUTHENTICATOR_TOKEN:'; repl='if False:'; tests=@($tApplication); select='test_authenticated_evidence_cannot_be_minted_directly' },
    @{ name='M18 trust store schema check removed'; file=$domain; find='if row\.get\("schema_version"\) != EVIDENCE_TRUST_STORE_SCHEMA_VERSION:'; repl='if False:'; tests=@($tDomain); select='test_trust_store_refuses_malformed_entries' },
    @{ name='M19 trust store accepts a bad trust status'; file=$domain; find='except \(TypeError, ValueError\) as error:\s*\n\s*raise EvidenceTrustStoreMalformed\(\s*\n\s*"trust store entry trust_status is invalid"\s*\n\s*\) from error'; repl="except (TypeError, ValueError):`n            trust_status = EvidenceKeyTrustStatus.ACTIVE"; tests=@($tDomain); select='test_trust_store_refuses_malformed_entries' },
    @{ name='M20 duplicate key ids accepted'; file=$domain; find='if key_id in seen:\s*\n\s*raise EvidenceTrustStoreMalformed\(f"duplicate key_id in trust store: \{key_id\}"\)'; repl="if False:`n            raise EvidenceTrustStoreMalformed(f`"duplicate key_id in trust store: {key_id}`")"; tests=@($tDomain); select='test_trust_store_refuses_a_duplicated_key_id_on_read' },

    # -- verification decision path --------------------------------------
    @{ name='M21 invalid signature accepted'; file=$application; find='if not valid:'; repl='if False:'; tests=@($tApplication); select='test_signature_change_fails_closed' },
    @{ name='M22 wrong public key accepted'; file=$application; find='if not valid:'; repl='if False:'; tests=@($tApplication); select='test_wrong_public_key_fails_closed' },
    @{ name='M23 revoked key no longer fails closed'; file=$application; find='if key\.trust_status is EvidenceKeyTrustStatus\.REVOKED:'; repl='if False:'; tests=@($tApplication); select='test_revoked_key_fails_closed' },
    @{ name='M24 unknown key no longer fails closed'; file=$application; find='if key is None:\s*\n\s*blockers\.add\(EvidenceAuthenticationBlocker\.UNKNOWN_KEY\)\s*\n\s*return'; repl="if key is None:`n            return"; tests=@($tApplication); select='test_unknown_key_fails_closed' },
    @{ name='M25 missing trust root reports the wrong reason'; file=$application; find='blockers\.add\(EvidenceAuthenticationBlocker\.TRUST_ROOT_UNAVAILABLE\)'; repl='pass'; tests=@($tApplication); select='test_missing_trust_root_fails_closed' },
    @{ name='M26 naive signed_at accepted'; file=$application; find='if signed_at\.tzinfo is None or signed_at\.utcoffset\(\) is None:'; repl='if False:'; tests=@($tApplication); select='test_naive_signed_at_fails_closed' },
    @{ name='M27 future signed_at accepted'; file=$application; find='signed_at\.astimezone\(timezone\.utc\)\s*\n\s*> verified_at\.astimezone\(timezone\.utc\) \+ policy\.maximum_clock_skew'; repl='False'; tests=@($tApplication); select='test_future_signed_at_fails_closed' },
    @{ name='M28 missing seal reports the wrong reason'; file=$application; find='blockers\.add\(EvidenceAuthenticationBlocker\.AUTHENTICATION_MISSING\)\s*\n\s*return None'; repl="blockers.add(EvidenceAuthenticationBlocker.SIGNATURE_MALFORMED)`n            return None"; tests=@($tApplication); select='test_missing_seal_fails_closed' },
    @{ name='M29 malformed seal no longer fails closed'; file=$application; find='blockers\.add\(EvidenceAuthenticationBlocker\.SIGNATURE_MALFORMED\)\s*\n\s*return None\s*\n\s*try:\s*\n\s*return seal_from_payload\(payload\)'; repl="return None`n        try:`n            return seal_from_payload(payload)"; tests=@($tApplication); select='test_malformed_seal_fails_closed' },
    @{ name='M30 authentication mutates strategy lifecycle state'; file=$application; find='blockers: set\[EvidenceAuthenticationBlocker\] = set\(\)'; repl="object.__setattr__(version, `"gate_passed`", True)`n        blockers: set[EvidenceAuthenticationBlocker] = set()"; tests=@($tApplication); select='test_authentication_does_not_change_strategy_lifecycle_fields' },

    # -- persistence ------------------------------------------------------
    @{ name='M31 conflicting immutable duplicate accepted'; file=$repository; find='raise EvidenceAuthenticationRepositoryConflict\(\s*\n\s*"authentication id already has a different immutable payload"\s*\n\s*\)'; repl='return'; tests=@($tRepository); select='test_same_id_with_different_payload_conflicts' },
    @{ name='M32 stored payload hash check removed'; file=$repository; find='if sha256\(payload_json\.encode\("utf-8"\)\)\.hexdigest\(\) != payload_hash:'; repl='if False:'; tests=@($tRepository); select='test_corrupt_payload_hash_fails_closed' },
    @{ name='M33 indexed column cross-check removed'; file=$repository; find='if indexed != stored:'; repl='if False:'; tests=@($tRepository); select='test_corrupt_indexed_columns_fail_closed' },
    @{ name='M34 retry idempotency broken by the verification clock'; file=$repository; find='payload\.pop\("verified_at", None\)'; repl='None'; tests=@($tRepository); select='test_semantic_retry_is_idempotent_and_keeps_first_timestamp' },
    @{ name='M35 newest-first ordering inverted'; file=$repository; find='return tuple\(sorted\(values, key=_sort_key, reverse=True\)\)'; repl='return tuple(sorted(values, key=_sort_key, reverse=False))'; tests=@($tRepository); select='test_revocation_row_coexists_with_the_earlier_pass' },

    # -- signing tool -----------------------------------------------------
    @{ name='M36 private key allowed inside a git tree'; file=$sealing; find='raise EvidenceSealingError\(\s*\n\s*"private key must not live inside a git working tree"\s*\n\s*\)'; repl='pass'; tests=@($tSealing); select='test_refuses_a_private_key_inside_a_git_working_tree' },
    @{ name='M37 private key allowed inside the artifact store'; file=$sealing; find='raise EvidenceSealingError\(\s*\n\s*"private key must not live inside the research artifact store"\s*\n\s*\)'; repl='pass'; tests=@($tSealing); select='test_refuses_a_private_key_inside_the_artifact_store' },
    @{ name='M38 verify-only key allowed to sign'; file=$sealing; find='if matching\.trust_status is not EvidenceKeyTrustStatus\.ACTIVE:'; repl='if False:'; tests=@($tSealing); select='test_refuses_a_verify_only_key' },
    @{ name='M39 revoked key allowed to sign'; file=$sealing; find='if matching\.trust_status is not EvidenceKeyTrustStatus\.ACTIVE:'; repl='if False:'; tests=@($tSealing); select='test_refuses_a_revoked_key' },
    @{ name='M40 private key not checked against the registered public key'; file=$sealing; find='if matching\.public_key != public_bytes:'; repl='if False:'; tests=@($tSealing); select='test_refuses_a_private_key_that_does_not_match_the_registered_public_key' },
    @{ name='M41 sealing tool signs a naive timestamp'; file=$sealing; find='if moment\.tzinfo is None or moment\.utcoffset\(\) is None:'; repl='if False:'; tests=@($tSealing); select='test_refuses_a_naive_signed_at' },
    @{ name='M42 sealing tool signs with a key the store does not list'; file=$sealing; find='if matching is None:'; repl='if False:'; tests=@($tSealing); select='test_refuses_an_unknown_key' },

    # -- architecture guards ---------------------------------------------
    @{ name='M43 runtime reaches the signing tool'; file=$composition; find='from us_quant\.trading\.adapters\.evidence_signature import \('; repl="from us_quant import research_evidence_sealing  # noqa: F401`nfrom us_quant.trading.adapters.evidence_signature import ("; tests=@($tArchitecture); select='test_b09_nothing_in_src_imports_the_signing_tool' },
    @{ name='M44 runtime imports a private signing primitive'; file=$signature; find='from cryptography\.hazmat\.primitives\.asymmetric\.ed25519 import Ed25519PublicKey'; repl="from cryptography.hazmat.primitives.asymmetric.ed25519 import (`n    Ed25519PrivateKey,`n    Ed25519PublicKey,`n)"; tests=@($tArchitecture); select='test_b06_signature_adapter_imports_only_public_verification_primitives' },
    @{ name='M45 authentication becomes promotion authority'; file=$application; find='from us_quant\.trading\.domain\.research_evidence import StrategyResearchEvidence'; repl="_LEGACY_PROMOTION_FIELD = `"gate_passed`"`nfrom us_quant.trading.domain.research_evidence import StrategyResearchEvidence"; tests=@($tArchitecture); select='test_b16_authentication_does_not_become_promotion_authority' },
    @{ name='M46 authentication absorbs the gate authority'; file=$application; find='from us_quant\.trading\.domain\.research_evidence import StrategyResearchEvidence'; repl="from us_quant.trading.domain.strategy_gate import StrategyGateEvaluation  # noqa: F401`nfrom us_quant.trading.domain.research_evidence import StrategyResearchEvidence"; tests=@($tArchitecture); select='test_b17_authentication_stays_independent_of_the_strategy_gate' },
    @{ name='M47 application reaches the artifact adapter directly'; file=$application; find='from us_quant\.trading\.domain import evidence_auth as _evidence_auth'; repl="from us_quant.trading.adapters.research_evidence import TargetedReviewArtifactSource  # noqa: F401`nfrom us_quant.trading.domain import evidence_auth as _evidence_auth"; tests=@($tArchitecture); select='test_b23_application_reaches_the_artifact_only_through_its_port' },

    # -- sealing tool file safety ----------------------------------------
    @{ name='M48 private key created world-readable'; file=$sealing; find='_PRIVATE_KEY_MODE = 0o600'; repl='_PRIVATE_KEY_MODE = 0o644'; tests=@($tSealing); select='test_private_key_is_created_owner_only_and_never_clobbered' },
    @{ name='M49 private key may be silently overwritten'; file=$sealing; find='destination, os\.O_WRONLY \| os\.O_CREAT \| os\.O_EXCL, _PRIVATE_KEY_MODE'; repl='destination, os.O_WRONLY | os.O_CREAT, _PRIVATE_KEY_MODE'; tests=@($tSealing); select='test_private_key_is_created_owner_only_and_never_clobbered' },
    @{ name='M50 trust store key id silently replaced'; file=$sealing; find='if any\(key\.key_id == args\.key_id for key in existing\):'; repl='if False:'; tests=@($tSealing); select='test_cli_refuses_to_replace_an_existing_key_id' },
    @{ name='M51 unparseable signed-at becomes a traceback'; file=$sealing; find='except ValueError as error:\s*\n\s*raise EvidenceSealingError\(\s*\n\s*f"--signed-at is not a valid ISO-8601 timestamp: \{args\.signed_at\}"\s*\n\s*\) from error'; repl="except ValueError:`n                pass"; tests=@($tSealing); select='test_cli_reports_an_unparseable_signed_at_without_traceback' }
)

function Get-Text([string]$path) { [System.IO.File]::ReadAllText($path) }
function Set-Text([string]$path, [string]$text) {
    [System.IO.File]::WriteAllText($path, $text, (New-Object System.Text.UTF8Encoding($false)))
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
    $backup = "$($mutation.file).stage6b1-backup"
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
