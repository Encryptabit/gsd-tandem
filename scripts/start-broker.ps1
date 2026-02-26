#!/usr/bin/env pwsh

[CmdletBinding()]
param(
    [Parameter()]
    [string]$BrokerDir,

    [Parameter()]
    [string]$ProjectRoot,

    [Parameter()]
    [string]$ConfigPath,

    [Parameter()]
    [string]$EnvName = ".venv-win",

    [Parameter()]
    [switch]$SkipSync,

    [Parameter()]
    [string]$BindHost = "0.0.0.0",

    [Parameter()]
    [ValidateRange(1, 65535)]
    [int]$Port = 8321,

    [Parameter()]
    [switch]$RestartIfRunning,

    [Parameter()]
    [switch]$VerboseBroker,

    [Parameter()]
    [ValidateSet("critical", "error", "warning", "info", "debug", "trace")]
    [string]$UvicornLogLevel = "warning"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Get-PropValue {
    param(
        [Parameter(Mandatory = $true)]
        [object]$Object,

        [Parameter(Mandatory = $true)]
        [string]$Name
    )

    if ($null -eq $Object) {
        return $null
    }

    $prop = $Object.PSObject.Properties[$Name]
    if ($null -eq $prop) {
        return $null
    }
    return $prop.Value
}

function Resolve-DefaultRepoRoot {
    if ([string]::IsNullOrWhiteSpace($PSScriptRoot)) {
        throw "Unable to resolve script root for repo defaults."
    }
    return (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
}

function Resolve-WorkspaceConfigPath {
    param(
        [Parameter(Mandatory = $true)]
        [string]$RootPath
    )

    $children = @(Get-ChildItem -LiteralPath $RootPath -Directory -ErrorAction SilentlyContinue | Sort-Object Name)
    foreach ($child in $children) {
        $candidate = Join-Path $child.FullName ".planning\config.json"
        if (-not (Test-Path -LiteralPath $candidate -PathType Leaf)) {
            continue
        }

        try {
            $cfg = Get-Content -LiteralPath $candidate -Raw -Encoding UTF8 | ConvertFrom-Json
        } catch {
            continue
        }

        $review = Get-PropValue -Object $cfg -Name "review"
        if ($null -eq $review) {
            continue
        }

        $reviewEnabled = Get-PropValue -Object $review -Name "enabled"
        if ($reviewEnabled -eq $false) {
            continue
        }

        $reviewerPool = Get-PropValue -Object $cfg -Name "reviewer_pool"
        if ($null -eq $reviewerPool) {
            continue
        }

        return (Resolve-Path -LiteralPath $candidate).Path
    }

    return $null
}

function Get-BrokerUserConfigDir {
    if (-not [string]::IsNullOrWhiteSpace($env:XDG_CONFIG_HOME)) {
        return Join-Path $env:XDG_CONFIG_HOME "gsd-review-broker"
    }

    if ($IsWindows -or $env:OS -eq "Windows_NT") {
        if (-not [string]::IsNullOrWhiteSpace($env:APPDATA)) {
            return Join-Path $env:APPDATA "gsd-review-broker"
        }
        return Join-Path (Join-Path $HOME "AppData\Roaming") "gsd-review-broker"
    }

    if ($IsMacOS) {
        return Join-Path (Join-Path $HOME "Library/Application Support") "gsd-review-broker"
    }

    return Join-Path (Join-Path $HOME ".config") "gsd-review-broker"
}

function Ensure-WorkspaceDefaultConfig {
    param(
        [Parameter(Mandatory = $true)]
        [string]$RootPath,

        [Parameter()]
        [string]$SeedConfigPath
    )

    $configDir = Get-BrokerUserConfigDir
    if (-not (Test-Path -LiteralPath $configDir -PathType Container)) {
        New-Item -ItemType Directory -Path $configDir -Force | Out-Null
    }

    $configPath = Join-Path $configDir "workspace-default-config.json"
    $reviewerPool = [ordered]@{}

    if (-not [string]::IsNullOrWhiteSpace($SeedConfigPath) -and (Test-Path -LiteralPath $SeedConfigPath -PathType Leaf)) {
        try {
            $seed = Get-Content -LiteralPath $SeedConfigPath -Raw -Encoding UTF8 | ConvertFrom-Json
            $seedPool = Get-PropValue -Object $seed -Name "reviewer_pool"
            if ($null -ne $seedPool) {
                foreach ($prop in $seedPool.PSObject.Properties) {
                    $reviewerPool[$prop.Name] = $prop.Value
                }
            }
        } catch {
            # Ignore malformed seed and continue with defaults.
        }
    } elseif (Test-Path -LiteralPath $configPath -PathType Leaf) {
        try {
            $existing = Get-Content -LiteralPath $configPath -Raw -Encoding UTF8 | ConvertFrom-Json
            $existingPool = Get-PropValue -Object $existing -Name "reviewer_pool"
            if ($null -ne $existingPool) {
                foreach ($prop in $existingPool.PSObject.Properties) {
                    $reviewerPool[$prop.Name] = $prop.Value
                }
            }
        } catch {
            # Ignore malformed existing config and overwrite.
        }
    }

    $reviewerPool["workspace_path"] = $RootPath
    if (-not $reviewerPool.Contains("prompt_template_path")) {
        $reviewerPool["prompt_template_path"] = "reviewer_prompt.md"
    }

    $payload = [ordered]@{
        reviewer_pool = $reviewerPool
    }
    $json = $payload | ConvertTo-Json -Depth 10
    Set-Content -LiteralPath $configPath -Encoding UTF8 -Value $json

    return (Resolve-Path -LiteralPath $configPath).Path
}

function Resolve-BrokerConfigPath {
    param(
        [Parameter(Mandatory = $true)]
        [string]$RootPath,

        [Parameter()]
        [string]$ExplicitConfigPath
    )

    if (-not [string]::IsNullOrWhiteSpace($ExplicitConfigPath)) {
        $candidate = if ([System.IO.Path]::IsPathRooted($ExplicitConfigPath)) {
            $ExplicitConfigPath
        } else {
            Join-Path $RootPath $ExplicitConfigPath
        }

        if (-not (Test-Path -LiteralPath $candidate -PathType Leaf)) {
            throw "Config path does not exist: $candidate"
        }
        return (Resolve-Path -LiteralPath $candidate).Path
    }

    $direct = Join-Path $RootPath ".planning\config.json"
    if (Test-Path -LiteralPath $direct -PathType Leaf) {
        return (Resolve-Path -LiteralPath $direct).Path
    }

    $seedConfigPath = Resolve-WorkspaceConfigPath -RootPath $RootPath
    return Ensure-WorkspaceDefaultConfig -RootPath $RootPath -SeedConfigPath $seedConfigPath
}

function Get-ListeningProcessDetails {
    param(
        [Parameter(Mandatory = $true)]
        [int]$LocalPort
    )

    $listener = Get-NetTCPConnection -LocalPort $LocalPort -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($null -eq $listener) {
        return $null
    }

    $owningPid = [int]$listener.OwningProcess
    $procInfo = Get-CimInstance Win32_Process -Filter "ProcessId=$owningPid" -ErrorAction SilentlyContinue
    if ($null -eq $procInfo) {
        return [pscustomobject]@{
            Pid = $owningPid
            Name = $null
            CommandLine = $null
            LocalAddress = $listener.LocalAddress
        }
    }

    return [pscustomobject]@{
        Pid = $owningPid
        Name = $procInfo.Name
        CommandLine = $procInfo.CommandLine
        LocalAddress = $listener.LocalAddress
    }
}

function Stop-ProcessTree {
    param(
        [Parameter(Mandatory = $true)]
        [int]$PidToStop
    )

    if ($PidToStop -le 0 -or $PidToStop -eq $PID) {
        return
    }

    $taskkill = Start-Process `
        -FilePath "taskkill.exe" `
        -ArgumentList @("/PID", "$PidToStop", "/T", "/F") `
        -NoNewWindow `
        -Wait `
        -PassThru `
        -ErrorAction SilentlyContinue

    if ($null -ne $taskkill -and $taskkill.ExitCode -eq 0) {
        return
    }

    Stop-Process -Id $PidToStop -Force -ErrorAction SilentlyContinue
}

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    throw "uv not found in PATH. Install uv and retry."
}

$defaultRepoRoot = Resolve-DefaultRepoRoot
if ([string]::IsNullOrWhiteSpace($BrokerDir)) {
    $BrokerDir = Join-Path $defaultRepoRoot "tools\gsd-review-broker"
}
if ([string]::IsNullOrWhiteSpace($ProjectRoot)) {
    $ProjectRoot = $defaultRepoRoot
}

$resolvedBrokerDir = (Resolve-Path -LiteralPath $BrokerDir).Path
$resolvedProjectRoot = (Resolve-Path -LiteralPath $ProjectRoot).Path
$resolvedConfigPath = Resolve-BrokerConfigPath -RootPath $resolvedProjectRoot -ExplicitConfigPath $ConfigPath
$legacyMixedVenvPath = Join-Path $resolvedBrokerDir ".venv\lib64"

$existingListener = Get-ListeningProcessDetails -LocalPort $Port
if ($null -ne $existingListener) {
    $cmdline = if ($null -ne $existingListener.CommandLine) { [string]$existingListener.CommandLine } else { "" }
    $isBroker = $cmdline -match '(?i)gsd[-_]review[-_]broker'

    if (-not $isBroker) {
        $existingName = if ([string]::IsNullOrWhiteSpace([string]$existingListener.Name)) { "unknown" } else { [string]$existingListener.Name }
        throw ("Port {0} is already in use by PID {1} ({2}). Choose another port or stop that process first." -f $Port, $existingListener.Pid, $existingName)
    }

    if (-not $RestartIfRunning) {
        Write-Host ("[{0}] Broker already running on {1}:{2} (pid={3}); leaving it as-is." -f (Get-Date -Format "u"), $existingListener.LocalAddress, $Port, $existingListener.Pid)
        exit 0
    }

    Write-Host ("[{0}] Restarting existing broker on port {1} (pid={2})..." -f (Get-Date -Format "u"), $Port, $existingListener.Pid)
    Stop-ProcessTree -PidToStop $existingListener.Pid
    Start-Sleep -Seconds 1
}

$env:UV_PROJECT_ENVIRONMENT = $EnvName
$env:BROKER_HOST = $BindHost
$env:BROKER_UVICORN_LOG_LEVEL = $UvicornLogLevel
$env:BROKER_REPO_ROOT = $resolvedProjectRoot
if ($null -ne $resolvedConfigPath) {
    $env:BROKER_CONFIG_PATH = $resolvedConfigPath
} else {
    Remove-Item Env:BROKER_CONFIG_PATH -ErrorAction SilentlyContinue
}

$globalPromptPath = Join-Path $env:APPDATA "gsd-review-broker\reviewer_prompt.md"
if (Test-Path -LiteralPath $globalPromptPath) {
    $env:BROKER_PROMPT_TEMPLATE_PATH = $globalPromptPath
}

Write-Host ("[{0}] Broker dir: {1}" -f (Get-Date -Format "u"), $resolvedBrokerDir)
Write-Host ("[{0}] Broker project root: {1}" -f (Get-Date -Format "u"), $env:BROKER_REPO_ROOT)
if ($null -ne $resolvedConfigPath) {
    Write-Host ("[{0}] Broker config path: {1}" -f (Get-Date -Format "u"), $env:BROKER_CONFIG_PATH)
} else {
    Write-Warning ("No .planning/config.json found under {0}; reviewer pool may be disabled." -f $resolvedProjectRoot)
}
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
$brokerArgs = @("--directory", $resolvedBrokerDir, "run", "gsd-review-broker")
if ($VerboseBroker) {
    $brokerArgs += "--verbose"
}
& uv @brokerArgs
exit $LASTEXITCODE
