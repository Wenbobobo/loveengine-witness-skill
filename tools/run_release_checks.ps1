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
$buildCheckRoot = Join-Path ([System.IO.Path]::GetTempPath()) (
    "loveengine-release-check-" + [guid]::NewGuid().ToString("N")
)
New-Item -ItemType Directory -Path $buildCheckRoot | Out-Null
Push-Location -LiteralPath $root
try {
    Invoke-CheckedNative "uv sync" { uv sync --frozen }
    $contractPreparation = Invoke-CheckedNative "contract preparation" {
        uv run loveengine pilot contracts prepare
    } | ConvertFrom-Json
    $forge = [string]$contractPreparation.toolchain.forge.path
    if (
        $contractPreparation.prepared -ne $true -or
        [string]::IsNullOrWhiteSpace($forge) -or
        -not (Test-Path -LiteralPath $forge)
    ) {
        throw "Contract preparation did not return usable Forge provenance."
    }

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

    Invoke-CheckedNative "accelerated soak" {
        uv run loveengine pilot soak `
            --stage core `
            --duration-seconds 1 `
            --events 12 `
            --observers 10 `
            --output (Join-Path $buildCheckRoot "accelerated-soak")
    }

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
