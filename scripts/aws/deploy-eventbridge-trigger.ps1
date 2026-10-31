# =============================================================================
# deploy-eventbridge-trigger.ps1 - plan/apply the S3-upload trigger stack
# -----------------------------------------------------------------------------
# Purpose: manages infra/eventbridge-trigger, the optional "event-driven start"
# of the batch pipeline. When ingest.ps1 -LocalAcquisition -Trigger EventBridge
# uploads pipeline/runs/<run>/raw/trigger.json to the data bucket:
#   S3 (EventBridge notifications on) -> EventBridge rule is459-g3-raw-upload-
#   trigger -> Step Functions wrapper is459-g3-raw-trigger -> starts the batch
#   state machine is459-g3-ingest with the run ID as execution name.
# The wrapper lets a redelivered event start the batch only once.
#
# Stack resources (8): S3 bucket notification, EventBridge rule + target, two
# IAM roles + policies (EventBridge -> wrapper, wrapper -> batch/SNS) and the
# wrapper state machine.
#
# Modes:
#   (default)  plan the deployment;              -Execute applies it
#   -Disable   plan with the rule DISABLED;      -Execute applies it
#   -Destroy   plan removal of the stack;        -Execute applies it
# The plan is checked so it can only touch these eight resources and only with
# the expected actions (create/update, or delete for -Destroy).
#
# Terraform state is private (%LOCALAPPDATA%\IS459-G3\terraform-eventbridge-
# trigger). This script never starts a pipeline run.
# =============================================================================
[CmdletBinding()]
param([string]$Profile = 'is459-g3-tools', [switch]$Disable, [switch]$Destroy, [switch]$Execute)
# Plans by default. -Execute applies the saved plan; -Destroy plans (and with -Execute applies) the teardown.
$ErrorActionPreference = 'Stop'
if ($Disable -and $Destroy) { throw '-Disable and -Destroy cannot be combined.' }
$projectRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Push-Location $projectRoot
try {
    # Pin profile/region and refuse to run against any other account.
    $env:AWS_PROFILE = $Profile
    $env:AWS_REGION = 'ap-southeast-1'
    $env:AWS_DEFAULT_REGION = 'ap-southeast-1'
    $account = & aws sts get-caller-identity --query Account --output text --profile $Profile
    if ($LASTEXITCODE -ne 0 -or $account.Trim() -ne '588738581042') { throw 'Wrong/unavailable AWS account' }
    # Private Terraform cache; shared S3 state is initialized below.
    $privateRoot = Join-Path $env:LOCALAPPDATA 'IS459-G3/terraform-eventbridge-trigger'
    $env:TF_DATA_DIR = Join-Path $privateRoot 'runtime'
    $terraform = Join-Path $projectRoot 'output/tools/terraform/terraform.exe'
    & (Join-Path $PSScriptRoot 'init-terraform.ps1') -Stack 'eventbridge-trigger' -Profile $Profile -Terraform $terraform -AllowEmptyState


    # Build the plan. The 'enabled' variable sets the rule state (false for
    # -Disable); -destroy plans the removal of everything in this stack.
    $name = if ($Destroy) { 'destroy' } elseif ($Disable) { 'disable' } else { 'apply' }
    $planPath = Join-Path $privateRoot "$name.tfplan"
    $arguments = @('-chdir=infra/eventbridge-trigger', 'plan', "-out=$planPath", "-var=enabled=$((-not $Disable).ToString().ToLowerInvariant())")
    if ($Destroy) { $arguments += '-destroy' }
    & $terraform @arguments | Out-Host
    if ($LASTEXITCODE -ne 0) { throw 'Terraform plan failed' }
    $plan = & $terraform '-chdir=infra/eventbridge-trigger' show -json $planPath | ConvertFrom-Json
    if ($LASTEXITCODE -ne 0) { throw 'Cannot read plan' }
    # Ignore unchanged ('no-op') resources; inspect only real changes.
    $changes = @($plan.resource_changes | Where-Object { ($_.change.actions -join ',') -ne 'no-op' })
    # Never replace or delete anything outside this stack; S3 data, the batch workflow and SNS are only referenced.
    $foreign = @($changes | Where-Object { $_.address -notmatch '^aws_(s3_bucket_notification\.eventbridge|cloudwatch_event_rule\.raw_upload|cloudwatch_event_target\.workflow|iam_role\.(events|workflow)|iam_role_policy\.(events|workflow)|sfn_state_machine\.raw_trigger)$' })
    if ($foreign.Count) { throw "Plan touches unexpected resources: $($foreign.address -join ', ')" }
    # Allowed action set per mode, then an upper bound on the change count.
    $expected = if ($Destroy) { 'delete' } else { 'create|update' }
    if (@($changes | Where-Object { ($_.change.actions -join ',') -notmatch "^($expected)$" }).Count) { throw "Plan contains actions other than $expected; inspect private state before retrying" }
    if ($changes.Count -gt 8) { throw 'Plan has more than the eight reviewed resources' }
    if (-not $Execute) { Write-Output "Plan saved ($($changes.Count) $name changes). No AWS writes. Re-run with -Execute to apply it."; return }

    # Apply exactly the saved, checked plan (no re-planning).
    & $terraform '-chdir=infra/eventbridge-trigger' apply $planPath
    if ($LASTEXITCODE -ne 0) { throw 'Apply failed; preserve state and inspect' }
    $message = switch ($name) {
        'destroy' { 'EventBridge trigger removed. Uploads no longer start the pipeline; direct and cloud runs are unchanged.' }
        'disable' { 'EventBridge rule disabled. Uploads are ignored until you deploy again without -Disable.' }
        default   { 'EventBridge trigger deployed. No pipeline run started; use ingest.ps1 -LocalAcquisition -Trigger EventBridge.' }
    }
    Write-Output $message
} finally { Pop-Location }
