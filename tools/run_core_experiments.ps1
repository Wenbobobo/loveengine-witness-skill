[CmdletBinding()]
param(
    [string]$OutputDir
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
Push-Location -LiteralPath $repoRoot
try {
    $arguments = @("run", "python", ".\tools\run_core_experiments.py")
    if ($OutputDir) {
        $arguments += @("--output", [System.IO.Path]::GetFullPath($OutputDir))
    }
    & uv @arguments
    if ($LASTEXITCODE -ne 0) {
        throw "core experiment failed with exit code $LASTEXITCODE"
    }
}
finally {
    Pop-Location
}
