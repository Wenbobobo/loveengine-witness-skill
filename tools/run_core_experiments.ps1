[CmdletBinding()]
param(
    [string]$OutputDir
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$runId = "core-" + (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssZ") +
    "-" + [guid]::NewGuid().ToString("N").Substring(0, 8)
$resolvedOutput = if ($OutputDir) {
    [System.IO.Path]::GetFullPath($OutputDir)
}
else {
    Join-Path $repoRoot ("tmp\core-experiments\" + $runId)
}
$runtimeRoot = Join-Path $resolvedOutput "runtime"
$logRoot = Join-Path $resolvedOutput "logs"
$reportPath = Join-Path $resolvedOutput "core-experiment-report.json"
$steps = [System.Collections.Generic.List[object]]::new()
$startedAt = (Get-Date).ToUniversalTime().ToString("o")

New-Item -ItemType Directory -Path $logRoot -Force | Out-Null

function Invoke-RecordedNative {
    param(
        [Parameter(Mandatory = $true)][string]$Label,
        [Parameter(Mandatory = $true)][string[]]$Arguments
    )

    $safeLabel = $Label -replace '[^a-zA-Z0-9_-]', '-'
    $logPath = Join-Path $logRoot ($safeLabel + ".log")
    # Windows PowerShell turns native stderr into ErrorRecord objects. Keep
    # those diagnostics in the log, but decide success from the process exit
    # code instead of $ErrorActionPreference.
    $previousErrorActionPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $lines = @(& uv @Arguments 2>&1 | ForEach-Object { $_.ToString() })
        $exitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }
    [System.IO.File]::WriteAllLines($logPath, $lines)
    $steps.Add([ordered]@{
        name = $Label
        command = "uv " + ($Arguments -join " ")
        exit_code = $exitCode
        log = $logPath
    })
    if ($exitCode -ne 0) {
        throw "$Label failed with exit code $exitCode"
    }
    return ($lines -join [Environment]::NewLine)
}

function ConvertFrom-LastJsonLine {
    param([Parameter(Mandatory = $true)][string]$Text)

    $line = $Text -split "`r?`n" |
        Where-Object { $_.TrimStart().StartsWith("{") } |
        Select-Object -Last 1
    if (-not $line) {
        throw "command did not emit a JSON object"
    }
    return $line | ConvertFrom-Json
}

Push-Location -LiteralPath $repoRoot
try {
    Invoke-RecordedNative "uv-sync" @("sync", "--frozen") | Out-Null
    $demoText = Invoke-RecordedNative "core-e2e" @(
        "run", "loveengine", "demo", "lan-pilot",
        "--stage", "core",
        "--events", "12",
        "--observers", "10",
        "--output", $runtimeRoot
    )
    $demo = ConvertFrom-LastJsonLine $demoText
    if (
        $demo.stage -ne "core" -or
        $demo.environment -ne "local_anvil" -or
        $demo.actors_simulated -ne $true -or
        $demo.offline_verification -ne "offline_integrity" -or
        $demo.chain_verification -ne "chain_consistency" -or
        $demo.trust_verification -ne "chain_verified" -or
        $demo.gate_ready -ne $true
    ) {
        throw "core E2E returned an unexpected verification boundary"
    }

    $offlineText = Invoke-RecordedNative "offline-transcript" @(
        "run", "loveengine", "pilot", "transcript", "verify",
        $demo.transcript_path
    )
    $offline = ConvertFrom-LastJsonLine $offlineText
    if ($offline.verification_level -ne "offline_integrity" -or $offline.trust_bound) {
        throw "offline transcript verification overstated trust"
    }

    Invoke-RecordedNative "tamper-and-policy-tests" @(
        "run", "pytest",
        "tests/test_core_transcript.py",
        "tests/test_trust_policy.py",
        "-q"
    ) | Out-Null

    $report = [ordered]@{
        schema_version = "loveengine.core-experiment-report/1"
        run_id = $runId
        status = "passed"
        environment = "local_anvil"
        actors_simulated = $true
        started_at = $startedAt
        completed_at = (Get-Date).ToUniversalTime().ToString("o")
        transcript_path = $demo.transcript_path
        verification = [ordered]@{
            offline = $demo.offline_verification
            rpc = $demo.chain_verification
            rpc_with_policy = $demo.trust_verification
        }
        observation_receipts = $demo.observation_receipts
        review_receipts = $demo.review_receipts
        gate_ready = $demo.gate_ready
        proves = @(
            "release-to-task trust binding on local Anvil",
            "Relay delivery and signed observation/review receipts",
            "artifact and evidence integrity through ProposalGate"
        )
        does_not_prove = @(
            "source statements are true",
            "actors are socially or organizationally independent",
            "public/testnet/production deployment or long-term availability"
        )
        steps = $steps
    }
}
catch {
    $report = [ordered]@{
        schema_version = "loveengine.core-experiment-report/1"
        run_id = $runId
        status = "failed"
        environment = "local_anvil"
        actors_simulated = $true
        started_at = $startedAt
        completed_at = (Get-Date).ToUniversalTime().ToString("o")
        error = $_.Exception.Message
        steps = $steps
    }
    $json = $report | ConvertTo-Json -Depth 10
    [System.IO.File]::WriteAllText($reportPath, $json + [Environment]::NewLine)
    throw
}
finally {
    Pop-Location
}

$reportJson = $report | ConvertTo-Json -Depth 10
[System.IO.File]::WriteAllText($reportPath, $reportJson + [Environment]::NewLine)
Write-Output $reportJson
