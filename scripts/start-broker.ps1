#!/usr/bin/env pwsh

[CmdletBinding()]
param(
    [Parameter()]
    [string]$BrokerDir = "C:\projects\gsd-tandem\tools\gsd-review-broker",

    [Parameter()]
    [string]$ProjectRoot = (Get-Location).Path,

    [Parameter()]
    [string]$EnvName = ".venv-win",

    [Parameter()]
    [switch]$SkipSync,

    [Parameter()]
    [string]$BindHost = "0.0.0.0",

    [Parameter()]
    [ValidateSet("critical", "error", "warning", "info", "debug", "trace")]
    [string]$UvicornLogLevel = "warning"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    throw "uv not found in PATH. Install uv and retry."
}

$resolvedBrokerDir = (Resolve-Path -Path $BrokerDir).Path
$resolvedProjectRoot = (Resolve-Path -Path $ProjectRoot).Path
$legacyMixedVenvPath = Join-Path $resolvedBrokerDir ".venv\lib64"

$env:UV_PROJECT_ENVIRONMENT = $EnvName
$env:BROKER_HOST = $BindHost
$env:BROKER_UVICORN_LOG_LEVEL = $UvicornLogLevel
$env:BROKER_REPO_ROOT = $resolvedProjectRoot

$globalPromptPath = Join-Path $env:APPDATA "gsd-review-broker\reviewer_prompt.md"
if (Test-Path -LiteralPath $globalPromptPath) {
    $env:BROKER_PROMPT_TEMPLATE_PATH = $globalPromptPath
}

Write-Host ("[{0}] Broker dir: {1}" -f (Get-Date -Format "u"), $resolvedBrokerDir)
Write-Host ("[{0}] Broker project root: {1}" -f (Get-Date -Format "u"), $env:BROKER_REPO_ROOT)
Write-Host ("[{0}] UV_PROJECT_ENVIRONMENT={1}" -f (Get-Date -Format "u"), $env:UV_PROJECT_ENVIRONMENT)
if (Test-Path -LiteralPath $legacyMixedVenvPath) {
    Write-Warning ("Detected legacy mixed .venv at {0}; using {1} so uv will not touch it." -f $legacyMixedVenvPath, $env:UV_PROJECT_ENVIRONMENT)
}

if (-not $SkipSync) {
    Write-Host ("[{0}] Running dependency sync..." -f (Get-Date -Format "u"))
    & uv --directory $resolvedBrokerDir sync --extra dev
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
}

Write-Host ("[{0}] Starting gsd-review-broker..." -f (Get-Date -Format "u"))
& uv --directory $resolvedBrokerDir run gsd-review-broker
exit $LASTEXITCODE
