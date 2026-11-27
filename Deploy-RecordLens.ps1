[CmdletBinding()]
param(
    [ValidateSet('Auto', 'Codex', 'Copilot')]
    [string]$Client = 'Auto',
    [switch]$InstallOnly
)

$ErrorActionPreference = 'Stop'
$version = '1.1.0'
$install = Join-Path $env:LOCALAPPDATA "RecordLens\$version"
$python = Join-Path $install 'Scripts\python.exe'
$server = Join-Path $install 'Scripts\recordlens.exe'
function Register-CopilotServer([string]$Command) {
    $directory = if ($env:COPILOT_HOME) { $env:COPILOT_HOME } else { Join-Path $env:USERPROFILE '.copilot' }
    $config = Join-Path $directory 'mcp-config.json'
    $exists = Test-Path -LiteralPath $config
    $document = if ($exists) {
        ConvertFrom-Json -InputObject (Get-Content -Raw -LiteralPath $config)
    } else {
        [pscustomobject]@{}
    }
    if ($document -isnot [pscustomobject]) { throw 'Copilot configuration must be a JSON object.' }
    if (-not $document.PSObject.Properties['mcpServers']) {
        $document | Add-Member -NotePropertyName mcpServers -NotePropertyValue ([pscustomobject]@{})
    }
    if ($document.mcpServers -isnot [pscustomobject]) { throw 'Copilot mcpServers must be a JSON object.' }
    $entry = [ordered]@{ type = 'stdio'; command = $Command; args = @(); tools = @('*') }
    $document.mcpServers | Add-Member -NotePropertyName recordlens -NotePropertyValue $entry -Force

    $null = New-Item -ItemType Directory -Path $directory -Force
    $temp = Join-Path $directory ("recordlens-$([guid]::NewGuid()).tmp")
    try {
        $json = ConvertTo-Json -InputObject $document -Depth 100
        [IO.File]::WriteAllText($temp, $json + [Environment]::NewLine)
        if ($exists) {
            [IO.File]::Replace($temp, $config, "$config.recordlens-$([guid]::NewGuid()).bak")
        } else {
            [IO.File]::Move($temp, $config)
        }
    } finally {
        if (Test-Path -LiteralPath $temp) { Remove-Item -LiteralPath $temp }
    }
    Write-Host "RecordLens is registered in Copilot Global ($config). Start a new Copilot chat or reload VS Code."
}

if (-not $InstallOnly -and $Client -ne 'Copilot') {
    $codex = Get-Command codex -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($Client -eq 'Auto') {
        $Client = if ($codex) { 'Codex' } else { 'Copilot' }
    }
    if ($Client -eq 'Codex' -and -not $codex) {
        throw 'The Codex CLI was not found on PATH. Use -Client Copilot or -InstallOnly.'
    }
}
$artifactDir = Join-Path ([System.IO.Path]::GetTempPath()) ([guid]::NewGuid().ToString())
$null = New-Item -ItemType Directory -Path $artifactDir

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
    if ($Client -eq 'Copilot') {
        Register-CopilotServer -Command $server
        return
    }

    $codexDir = if ($env:CODEX_HOME) { $env:CODEX_HOME } else { Join-Path $env:USERPROFILE '.codex' }
    $config = Join-Path $codexDir 'config.toml'
    $backup = $null
    if (Test-Path -LiteralPath $config) {
        $backup = "$config.recordlens-$([guid]::NewGuid()).bak"
        Copy-Item -LiteralPath $config -Destination $backup
    }
    & $codex.Source mcp add recordlens -- $server
    if ($LASTEXITCODE -ne 0) {
        if ($backup) { Copy-Item -LiteralPath $backup -Destination $config -Force }
        throw 'Codex MCP registration failed.'
    }
    Write-Host 'RecordLens is registered for local Codex projects. Restart the VS Code extension.'
} finally {
    Get-ChildItem -LiteralPath $artifactDir -File | Remove-Item
    Remove-Item -LiteralPath $artifactDir
}
