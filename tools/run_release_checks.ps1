[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"

function Invoke-CheckedNative {
    param(
        [Parameter(Mandatory = $true)][string]$Label,
        [Parameter(Mandatory = $true)][scriptblock]$Command
    )
    & $Command
    if ($LASTEXITCODE -ne 0) {
        throw "$Label failed with exit code $LASTEXITCODE"
    }
}

$root = Split-Path -Parent $PSScriptRoot
$forgeCandidates = @()
if ($env:FOUNDRY_BIN) {
    $forgeCandidates += Join-Path $env:FOUNDRY_BIN "forge.exe"
}
$forgeCandidates += Join-Path $HOME ".codex\tools\foundry-v1.7.1\forge.exe"
$forgeCandidates += Join-Path $HOME ".foundry\bin\forge.exe"
$forge = $forgeCandidates | Where-Object { Test-Path -LiteralPath $_ } |
    Select-Object -First 1
if (-not $forge) {
    $forgeCommand = Get-Command forge -ErrorAction SilentlyContinue
    if ($forgeCommand) {
        $forge = $forgeCommand.Source
    }
}
if (-not $forge) {
    throw "Foundry 1.7.1 forge binary not found; set FOUNDRY_BIN."
}
$forgeVersion = & $forge --version
if ($LASTEXITCODE -ne 0 -or $forgeVersion[0] -notmatch '^forge Version: 1\.7\.1$') {
    throw "Foundry version mismatch; expected forge 1.7.1."
}

$buildCheckRoot = Join-Path ([System.IO.Path]::GetTempPath()) (
    "loveengine-release-check-" + [guid]::NewGuid().ToString("N")
)
New-Item -ItemType Directory -Path $buildCheckRoot | Out-Null
Push-Location -LiteralPath $root
try {
    Invoke-CheckedNative "uv sync" { uv sync --frozen }
    Invoke-CheckedNative "repository checks" { uv run python .\tools\check.py }
    Invoke-CheckedNative "non-integration tests" { uv run pytest -m "not integration" -q }

    Push-Location -LiteralPath (Join-Path $root "contracts")
    try {
        Invoke-CheckedNative "Foundry tests" { & $forge test }
    }
    finally {
        Pop-Location
    }

    Invoke-CheckedNative "M2-M6 integration tests" { uv run pytest -m integration -q }

    $first = Invoke-CheckedNative "first deterministic build" {
        uv run loveengine package build --output (Join-Path $buildCheckRoot "first")
    } | ConvertFrom-Json
    $second = Invoke-CheckedNative "second deterministic build" {
        uv run loveengine package build --output (Join-Path $buildCheckRoot "second")
    } | ConvertFrom-Json
    if (
        $first.archive_sha256 -ne $second.archive_sha256 -or
        $first.archive_keccak256 -ne $second.archive_keccak256
    ) {
        throw "Deterministic package double-build mismatch."
    }

    Invoke-CheckedNative "secret scan" { uv run python .\tools\scan_secrets.py }
    Invoke-CheckedNative "git diff check" { git diff --check }
}
finally {
    Pop-Location
    $resolvedTemp = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath())
    $resolvedBuild = [System.IO.Path]::GetFullPath($buildCheckRoot)
    if (-not $resolvedBuild.StartsWith($resolvedTemp, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to remove release-check path outside the temp directory."
    }
    Remove-Item -LiteralPath $resolvedBuild -Recurse -Force
}
