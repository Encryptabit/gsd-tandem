#!/usr/bin/env pwsh

[CmdletBinding()]
param(
    [Parameter()]
    [int[]]$Ports = @(8321),

    [Parameter()]
    [switch]$DryRun,

    [Parameter()]
    [switch]$Quiet
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Write-Log {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Message,

        [Parameter()]
        [ValidateSet("INFO", "WARN", "ERROR")]
        [string]$Level = "INFO"
    )

    if ($Quiet -and $Level -eq "INFO") {
        return
    }

    Write-Host ("[{0}] [{1}] {2}" -f (Get-Date -Format "u"), $Level, $Message)
}

function Get-BrokerProcessSnapshot {
    $brokerNamePattern = '(?i)^gsd-review-broker\.exe$'
    $brokerTokenPattern = '(?i)gsd[-_]review[-_]broker'
    $brokerPathPattern = '(?i)(\\|/)tools(\\|/)gsd-review-broker(\\|/)'

    return @(Get-CimInstance Win32_Process | Where-Object {
            if ($null -eq $_) { return $false }
            $name = if ($null -ne $_.Name) { [string]$_.Name } else { "" }
            $cmd = if ($null -ne $_.CommandLine) { [string]$_.CommandLine } else { "" }

            return (
                ($name -match $brokerNamePattern) -or
                ($cmd -match $brokerTokenPattern) -or
                ($cmd -match $brokerPathPattern)
            )
        })
}

function Get-ListeningPids {
    param(
        [Parameter(Mandatory = $true)]
        [int[]]$LocalPorts
    )

    $ids = [System.Collections.Generic.HashSet[int]]::new()
    foreach ($port in $LocalPorts) {
        $listeners = @(Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue)
        foreach ($listener in $listeners) {
            if ($null -eq $listener) { continue }
            $pid = [int]$listener.OwningProcess
            if ($pid -gt 0) {
                [void]$ids.Add($pid)
            }
        }
    }

    return @($ids)
}

function Stop-ProcessTreeByPid {
    param(
        [Parameter(Mandatory = $true)]
        [int]$PidToStop,

        [Parameter()]
        [string]$Reason = "unspecified"
    )

    if ($PidToStop -le 0 -or $PidToStop -eq $PID) {
        return $false
    }

    if ($DryRun) {
        Write-Log ("DryRun: would terminate PID {0} ({1})" -f $PidToStop, $Reason)
        return $true
    }

    $taskKillResult = Start-Process `
        -FilePath "taskkill.exe" `
        -ArgumentList @("/PID", "$PidToStop", "/T", "/F") `
        -NoNewWindow `
        -Wait `
        -PassThru `
        -ErrorAction SilentlyContinue

    if ($null -ne $taskKillResult -and $taskKillResult.ExitCode -eq 0) {
        Write-Log ("Terminated PID tree {0} ({1})" -f $PidToStop, $Reason)
        return $true
    }

    try {
        Stop-Process -Id $PidToStop -Force -ErrorAction Stop
        Write-Log ("Force-stopped PID {0} ({1}) using Stop-Process fallback" -f $PidToStop, $Reason) "WARN"
        return $true
    } catch {
        Write-Log ("Failed to stop PID {0} ({1}): {2}" -f $PidToStop, $Reason, $_.Exception.Message) "WARN"
        return $false
    }
}

$targetRootIds = [System.Collections.Generic.HashSet[int]]::new()

$matchedProcesses = @(Get-BrokerProcessSnapshot | Where-Object { $_.ProcessId -ne $PID })
foreach ($proc in $matchedProcesses) {
    [void]$targetRootIds.Add([int]$proc.ProcessId)
}

$listenerPids = @(Get-ListeningPids -LocalPorts $Ports | Where-Object { $_ -ne $PID })
foreach ($pid in $listenerPids) {
    [void]$targetRootIds.Add([int]$pid)
}

if ($targetRootIds.Count -eq 0) {
    Write-Log "No broker-related process roots found."
    exit 0
}

Write-Log ("Found {0} broker process root(s): {1}" -f $targetRootIds.Count, (($targetRootIds | Sort-Object) -join ", "))

foreach ($pid in ($targetRootIds | Sort-Object -Descending)) {
    [void](Stop-ProcessTreeByPid -PidToStop $pid -Reason "root target")
}

# Secondary sweep for leftovers that may not have been part of a clean tree.
$leftoverProcesses = @(Get-BrokerProcessSnapshot | Where-Object { $_.ProcessId -ne $PID })
if ($leftoverProcesses.Count -gt 0) {
    Write-Log ("Secondary sweep: {0} leftover broker-related process(es)." -f $leftoverProcesses.Count) "WARN"
    foreach ($proc in ($leftoverProcesses | Sort-Object ProcessId -Descending)) {
        [void](Stop-ProcessTreeByPid -PidToStop ([int]$proc.ProcessId) -Reason "secondary sweep")
    }
}

if ($DryRun) {
    Write-Log "DryRun complete; no processes were terminated."
    exit 0
}

$remainingListeners = @()
foreach ($port in $Ports) {
    $remainingListeners += @(Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue)
}
$remainingBrokerProcs = @(Get-BrokerProcessSnapshot | Where-Object { $_.ProcessId -ne $PID })

if ($remainingListeners.Count -eq 0 -and $remainingBrokerProcs.Count -eq 0) {
    Write-Log "Broker shutdown confirmed: no listening target ports and no broker-related processes."
    exit 0
}

if ($remainingListeners.Count -gt 0) {
    $listenerSummary = $remainingListeners |
        Select-Object LocalAddress, LocalPort, OwningProcess |
        Sort-Object LocalPort, OwningProcess
    Write-Log ("Remaining listeners detected: {0}" -f (($listenerSummary | Out-String).Trim())) "ERROR"
}

if ($remainingBrokerProcs.Count -gt 0) {
    $procSummary = $remainingBrokerProcs |
        Select-Object ProcessId, Name, CommandLine |
        Sort-Object ProcessId
    Write-Log ("Remaining broker-related processes detected: {0}" -f (($procSummary | Out-String).Trim())) "ERROR"
}

exit 1
