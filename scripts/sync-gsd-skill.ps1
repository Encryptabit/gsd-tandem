#!/usr/bin/env pwsh

[CmdletBinding()]
param(
    [Parameter()]
    [string]$Source = (Join-Path $HOME ".claude/get-shit-done"),

    [Parameter()]
    [string]$Target = (Join-Path $HOME ".codex/skills/gsd"),

    [Parameter()]
    [switch]$OverwriteSkillMd,

    [Parameter()]
    [switch]$DryRun
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if (-not (Test-Path -LiteralPath $Source -PathType Container)) {
    throw "Source directory not found: $Source"
}

if (-not (Test-Path -LiteralPath $Target -PathType Container)) {
    New-Item -ItemType Directory -Path $Target -Force | Out-Null
}

$sourceResolved = (Resolve-Path -LiteralPath $Source).Path
$targetResolved = (Resolve-Path -LiteralPath $Target).Path

$args = @(
    $sourceResolved
    $targetResolved
    "/MIR"
    "/R:2"
    "/W:1"
    "/NP"
    "/NJH"
    "/NJS"
)

if (-not $OverwriteSkillMd) {
    $args += @("/XF", "SKILL.md")
}

if ($DryRun) {
    $args += "/L"
}

Write-Host ("Syncing GSD skill: {0} -> {1}" -f $sourceResolved, $targetResolved)
if (-not $OverwriteSkillMd) {
    Write-Host "Preserving target SKILL.md (use -OverwriteSkillMd to replace it)."
}
if ($DryRun) {
    Write-Host "Dry run enabled; no files will be changed."
}

& robocopy @args
$exitCode = $LASTEXITCODE

# Robocopy exit codes 0-7 are success/warnings; 8+ are failures.
if ($exitCode -ge 8) {
    throw "Robocopy failed with exit code $exitCode."
}

Write-Host ("Sync complete (robocopy exit code: {0})." -f $exitCode)
