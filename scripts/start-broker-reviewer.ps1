#!/usr/bin/env pwsh

[CmdletBinding()]
param(
    [Parameter()]
    [string]$ReviewerId = "codex-r1",

    [Parameter()]
    [string]$Category = "",

    [Parameter()]
    [string]$Model = "gpt-5.3-codex",

    [Parameter()]
    [string]$ReasoningEffort = "high",

    [Parameter()]
    [string]$Distro = "Ubuntu",

    [Parameter()]
    [string]$WorkspaceWslPath = "/mnt/c/Projects/gsd-tandem",

    [Parameter()]
    [ValidateRange(0, 3600)]
    [int]$RestartDelaySeconds = 2,

    [Parameter()]
    [switch]$DryRun
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Escape-BashSingleQuoted {
    param([Parameter(Mandatory = $true)][string]$Value)
    return $Value -replace "'", "'""'""'"
}

$reviewerEsc = Escape-BashSingleQuoted -Value $ReviewerId
$modelEsc = Escape-BashSingleQuoted -Value $Model
$reasoningEffortEsc = Escape-BashSingleQuoted -Value $ReasoningEffort
$workspaceEsc = Escape-BashSingleQuoted -Value $WorkspaceWslPath

$listReviewsStep = if ([string]::IsNullOrWhiteSpace($Category)) {
    '1) list_reviews(status="pending", wait=true).'
} else {
    $categoryForPrompt = $Category.Replace('\', '\\').Replace('"', '\"')
    "1) list_reviews(status=`"pending`", wait=true, category=`"$categoryForPrompt`")."
}

$promptText = @(
    "You are reviewer `"$ReviewerId`". Loop indefinitely:"
    $listReviewsStep
    "2) When reviews appear, process reviews in returned order; for each ID, call claim_review(review_id=ID, reviewer_id=`"$ReviewerId`"). If claim fails, skip."
    "3) get_proposal(review_id=ID)."
    "4) Perform a thorough review focused on correctness, regressions, security/privacy, data integrity, and missing tests."
    "5) If you find a problem and can fix it quickly, prefer submit_verdict(review_id=ID, verdict=`"changes_requested`", reason=..., counter_patch=UNIFIED_DIFF). Include a concrete patch whenever feasible so proposer can copy/apply it directly."
    "6) Use verdict=`"comment`" only for non-blocking suggestions or uncertain ideas. If useful, attach counter_patch there too."
    "7) Use verdict=`"approved`" when no blocking issues remain."
    "8) Only close_review(review_id=ID) after approved. Do NOT close after changes_requested/comment; leave it open for proposer to accept/reject counter-patch or resubmit."
    "9) Loop Infinitely."
    "Counter-patch rules: keep patches minimal and valid unified diffs that apply cleanly; if patch validation fails, still submit changes_requested with exact file-level guidance in reason."
    "Always include reasoning in verdict reason and prioritize catching real risks over speed."
) -join "`n"

$promptB64 = [Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes($promptText))
$promptB64Esc = Escape-BashSingleQuoted -Value $promptB64

$bashTemplate = @'
if [ -s ~/.nvm/nvm.sh ]; then . ~/.nvm/nvm.sh; fi; printf '%s' '__PROMPT_B64__' | base64 -d | codex exec --sandbox read-only --ephemeral --model '__MODEL__' -c 'model_reasoning_effort=__REASONING_EFFORT__' -C '__WORKSPACE_WSL_PATH__' -
'@

$bashCommand = $bashTemplate.
    Replace('__PROMPT_B64__', $promptB64Esc).
    Replace('__MODEL__', $modelEsc).
    Replace('__REASONING_EFFORT__', $reasoningEffortEsc).
    Replace('__WORKSPACE_WSL_PATH__', $workspaceEsc)

if ($DryRun) {
    Write-Host "Dry run mode enabled. Command to execute in WSL:"
    Write-Host $bashCommand
    exit 0
}

while ($true) {
    Write-Host ("[{0}] Starting broker reviewer '{1}'..." -f (Get-Date -Format "u"), $ReviewerId)
    & wsl.exe -d $Distro -- bash -lc $bashCommand
    $exitCode = $LASTEXITCODE

    Write-Warning ("[{0}] Broker reviewer exited with code {1}. Restarting in {2}s. Press Ctrl+C to stop this launcher." -f (Get-Date -Format "u"), $exitCode, $RestartDelaySeconds)
    if ($RestartDelaySeconds -gt 0) {
        Start-Sleep -Seconds $RestartDelaySeconds
    }
}
