#!/usr/bin/env pwsh

[CmdletBinding()]
param(
    [Parameter()]
    [string]$RepoRoot,

    [Parameter()]
    [string]$InstallRoot,

    [Parameter()]
    [switch]$GetShitDone,

    [Parameter()]
    [switch]$Broker,

    [Parameter()]
    [switch]$DryRun,

    [Parameter()]
    [switch]$VerboseOutput
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($RepoRoot)) {
    $scriptPath = $MyInvocation.MyCommand.Path
    if ([string]::IsNullOrWhiteSpace($scriptPath)) {
        throw "Unable to resolve script path for RepoRoot default."
    }
    $scriptDir = Split-Path -Parent $scriptPath
    $RepoRoot = (Resolve-Path -LiteralPath (Join-Path $scriptDir "..")).Path
}

if ([string]::IsNullOrWhiteSpace($InstallRoot)) {
    $InstallRoot = Join-Path $HOME ".claude"
}

if (-not (Get-Command robocopy -ErrorAction SilentlyContinue)) {
    throw "robocopy not found in PATH."
}

function Invoke-MirrorSync {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Name,

        [Parameter(Mandatory = $true)]
        [string]$Source,

        [Parameter(Mandatory = $true)]
        [string]$Target,

        [Parameter()]
        [string[]]$ExcludeDirs = @(),

        [Parameter()]
        [string[]]$ExcludeFiles = @(),

        [Parameter()]
        [switch]$DryRunMode,

        [Parameter()]
        [switch]$VerboseMode,

        [Parameter()]
        [switch]$MirrorMode
    )

    if (-not (Test-Path -LiteralPath $Source -PathType Container)) {
        throw "Source directory not found for '$Name': $Source"
    }

    if (-not (Test-Path -LiteralPath $Target -PathType Container)) {
        New-Item -ItemType Directory -Path $Target -Force | Out-Null
    }

    $sourceResolved = (Resolve-Path -LiteralPath $Source).Path
    $targetResolved = (Resolve-Path -LiteralPath $Target).Path

    $args = @(
        $sourceResolved
        $targetResolved
        "/R:2"
        "/W:1"
        "/NP"
    )

    if ($MirrorMode) {
        $args += "/MIR"
    } else {
        # Non-destructive sync for install roots that contain runtime-generated files.
        $args += "/E"
    }

    if (-not $VerboseMode) {
        $args += @("/NJH", "/NJS")
    }

    if ($ExcludeDirs.Count -gt 0) {
        $args += "/XD"
        $args += $ExcludeDirs
    }

    if ($ExcludeFiles.Count -gt 0) {
        $args += "/XF"
        $args += $ExcludeFiles
    }

    if ($DryRunMode) {
        $args += "/L"
    }

    Write-Host ("Syncing {0}: {1} -> {2}" -f $Name, $sourceResolved, $targetResolved)
    if ($ExcludeDirs.Count -gt 0 -or $ExcludeFiles.Count -gt 0) {
        Write-Host ("  Excluding dirs: {0}" -f (($ExcludeDirs -join ", ")))
        Write-Host ("  Excluding files: {0}" -f (($ExcludeFiles -join ", ")))
    }
    if ($DryRunMode) {
        Write-Host "  Dry run enabled; no files will be changed."
    }

    & robocopy @args
    $exitCode = $LASTEXITCODE

    # Robocopy exit codes 0-7 are success/warnings; 8+ are failures.
    if ($exitCode -ge 8) {
        throw "Robocopy failed for '$Name' with exit code $exitCode."
    }

    Write-Host ("  Done ({0}, robocopy exit code: {1})" -f $Name, $exitCode)
}

# Default behavior: sync both targets if no explicit selection provided.
if (-not $GetShitDone -and -not $Broker) {
    $GetShitDone = $true
    $Broker = $true
}

$repoRootResolved = (Resolve-Path -LiteralPath $RepoRoot).Path

if ($GetShitDone) {
    Invoke-MirrorSync `
        -Name "get-shit-done" `
        -Source (Join-Path $repoRootResolved "get-shit-done") `
        -Target (Join-Path $InstallRoot "get-shit-done") `
        -MirrorMode:$false `
        -DryRunMode:$DryRun `
        -VerboseMode:$VerboseOutput
}

if ($Broker) {
    Invoke-MirrorSync `
        -Name "gsd-review-broker" `
        -Source (Join-Path (Join-Path $repoRootResolved "tools") "gsd-review-broker") `
        -Target (Join-Path (Join-Path $InstallRoot "tools") "gsd-review-broker") `
        -ExcludeDirs @(
            ".venv",
            ".venv-linux",
            ".venv-win",
            ".venv-win2",
            ".venv-wsl",
            ".venv_local",
            "__pycache__",
            ".pytest_cache",
            ".ruff_cache",
            ".planning"
        ) `
        -ExcludeFiles @(
            "*.pyc",
            "*.pyo"
        ) `
        -MirrorMode:$true `
        -DryRunMode:$DryRun `
        -VerboseMode:$VerboseOutput
}

Write-Host "Sync complete."
