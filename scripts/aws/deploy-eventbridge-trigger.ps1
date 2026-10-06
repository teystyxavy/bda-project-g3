[CmdletBinding()]
param([string]$Profile = 'is459-g3-tools', [switch]$Disable, [switch]$Destroy, [switch]$Execute)
# Plans by default. -Execute applies the saved plan; -Destroy plans (and with -Execute applies) the teardown.
$ErrorActionPreference = 'Stop'
if ($Disable -and $Destroy) { throw '-Disable and -Destroy cannot be combined.' }
$projectRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Push-Location $projectRoot
try {
    $env:AWS_PROFILE = $Profile
    $env:AWS_REGION = 'ap-southeast-1'
    $env:AWS_DEFAULT_REGION = 'ap-southeast-1'
    $account = & aws sts get-caller-identity --query Account --output text --profile $Profile
    if ($LASTEXITCODE -ne 0 -or $account.Trim() -ne '588738581042') { throw 'Wrong/unavailable AWS account' }
    $privateRoot = Join-Path $env:LOCALAPPDATA 'IS459-G3/terraform-eventbridge-trigger'
    $env:TF_DATA_DIR = Join-Path $privateRoot 'runtime'
    $terraform = Join-Path $projectRoot 'output/tools/terraform/terraform.exe'
    $statePath = Join-Path $privateRoot 'state/terraform.tfstate'
    $providerMirror = Join-Path $env:LOCALAPPDATA 'IS459-G3/terraform-orchestration/providers'
    $initArgs = @('-chdir=infra/eventbridge-trigger', 'init', "-backend-config=path=$($statePath.Replace('\','/'))")
    if (Test-Path -LiteralPath $providerMirror) { $initArgs += "-plugin-dir=$($providerMirror.Replace('\','/'))" }
    & $terraform @initArgs
    if ($LASTEXITCODE -ne 0) { throw 'Terraform initialization failed' }

    $name = if ($Destroy) { 'destroy' } elseif ($Disable) { 'disable' } else { 'apply' }
    $planPath = Join-Path $privateRoot "$name.tfplan"
    $arguments = @('-chdir=infra/eventbridge-trigger', 'plan', "-out=$planPath", "-var=enabled=$((-not $Disable).ToString().ToLowerInvariant())")
    if ($Destroy) { $arguments += '-destroy' }
    & $terraform @arguments | Out-Host
    if ($LASTEXITCODE -ne 0) { throw 'Terraform plan failed' }
    $plan = & $terraform '-chdir=infra/eventbridge-trigger' show -json $planPath | ConvertFrom-Json
    if ($LASTEXITCODE -ne 0) { throw 'Cannot read plan' }
    $changes = @($plan.resource_changes | Where-Object { ($_.change.actions -join ',') -ne 'no-op' })
    # Never replace or delete anything outside this stack; S3 data, the batch workflow and SNS are only referenced.
    $foreign = @($changes | Where-Object { $_.address -notmatch '^aws_(s3_bucket_notification\.eventbridge|cloudwatch_event_rule\.raw_upload|cloudwatch_event_target\.workflow|iam_role\.(events|workflow)|iam_role_policy\.(events|workflow)|sfn_state_machine\.raw_trigger)$' })
    if ($foreign.Count) { throw "Plan touches unexpected resources: $($foreign.address -join ', ')" }
    $expected = if ($Destroy) { 'delete' } else { 'create|update' }
    if (@($changes | Where-Object { ($_.change.actions -join ',') -notmatch "^($expected)$" }).Count) { throw "Plan contains actions other than $expected; inspect private state before retrying" }
    if ($changes.Count -gt 8) { throw 'Plan has more than the eight reviewed resources' }
    if (-not $Execute) { Write-Output "Plan saved ($($changes.Count) $name changes). No AWS writes. Re-run with -Execute to apply it."; return }

    & $terraform '-chdir=infra/eventbridge-trigger' apply $planPath
    if ($LASTEXITCODE -ne 0) { throw 'Apply failed; preserve state and inspect' }
    $message = switch ($name) {
        'destroy' { 'EventBridge trigger removed. Uploads no longer start the pipeline; direct and cloud runs are unchanged.' }
        'disable' { 'EventBridge rule disabled. Uploads are ignored until you deploy again without -Disable.' }
        default   { 'EventBridge trigger deployed. No pipeline run started; use ingest.ps1 -LocalAcquisition -Trigger EventBridge.' }
    }
    Write-Output $message
} finally { Pop-Location }
