[CmdletBinding()]
param([switch]$InstallOnly)

$ErrorActionPreference = 'Stop'
$version = '1.1.0'
$install = Join-Path $env:LOCALAPPDATA "RecordLens\$version"
$python = Join-Path $install 'Scripts\python.exe'
$server = Join-Path $install 'Scripts\recordlens.exe'
$artifactDir = Join-Path ([System.IO.Path]::GetTempPath()) ([guid]::NewGuid().ToString())
$null = New-Item -ItemType Directory -Path $artifactDir
if (-not $InstallOnly) {
    $codex = (Get-Command codex -ErrorAction Stop).Source
}
try {
    if (-not (Test-Path -LiteralPath $python)) {
        & python -m venv $install
        if ($LASTEXITCODE -ne 0) { throw 'Could not create the RecordLens environment.' }
    }
    & $python -m pip wheel --no-deps --wheel-dir $artifactDir $PSScriptRoot
    if ($LASTEXITCODE -ne 0) { throw 'RecordLens wheel build failed.' }
    $wheel = @(Get-ChildItem -LiteralPath $artifactDir -Filter 'mcp_recordlens-*.whl')
    if ($wheel.Count -ne 1) { throw 'Expected exactly one RecordLens wheel.' }
    & $python -m pip install --force-reinstall $wheel[0].FullName
    if ($LASTEXITCODE -ne 0) { throw 'RecordLens wheel install failed.' }
    & $python -m unittest discover -s (Join-Path $PSScriptRoot 'tests')
    if ($LASTEXITCODE -ne 0) { throw 'RecordLens tests failed.' }
    if ($InstallOnly) { return }

    $codexDir = if ($env:CODEX_HOME) { $env:CODEX_HOME } else { Join-Path $env:USERPROFILE '.codex' }
    $config = Join-Path $codexDir 'config.toml'
    $backup = $null
    if (Test-Path -LiteralPath $config) {
        $backup = "$config.recordlens-$([guid]::NewGuid()).bak"
        Copy-Item -LiteralPath $config -Destination $backup
    }
    & $codex mcp add recordlens -- $server
    if ($LASTEXITCODE -ne 0) {
        if ($backup) { Copy-Item -LiteralPath $backup -Destination $config -Force }
        throw 'Codex MCP registration failed.'
    }
    Write-Host 'RecordLens is registered for local Codex projects. Restart the VS Code extension.'
} finally {
    Get-ChildItem -LiteralPath $artifactDir -File | Remove-Item
    Remove-Item -LiteralPath $artifactDir
}
