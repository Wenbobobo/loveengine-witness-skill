[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
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
Push-Location -LiteralPath $root
try {
    uv sync --frozen
    uv run python .\tools\check.py
    uv run pytest -m "not integration" -q

    Push-Location -LiteralPath (Join-Path $root "contracts")
    try {
        & $forge test
    }
    finally {
        Pop-Location
    }

    uv run pytest .\tests\test_demo.py -q
    uv run pytest .\tests\integration\test_network_demo.py -q
    uv run pytest .\tests\integration\test_live_evidence_demo.py -q
    uv run pytest .\tests\integration\test_pilot_chain.py -q
    uv run pytest .\tests\integration\test_pilot_demo.py -q
    uv run pytest .\tests\integration\test_pilot_soak.py -q
    git diff --check
}
finally {
    Pop-Location
}
