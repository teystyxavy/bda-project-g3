# =============================================================================
# update-ingestion.ps1 - update an already deployed batch ingestion stack
# -----------------------------------------------------------------------------
# Purpose: rolls out a new build (new code, model or worker image) to the
# existing infra/ingestion stack, without destroying anything. Used instead of
# deploy-ingestion.ps1 once the stack exists.
#
# Inputs:
#   -PreparedDirectory  new output of scripts/aws/prepare-ingestion.py
#   -Image              local worker image whose digest deployment.json pins
#   -Profile            AWS CLI profile;  -Execute  push image + apply.
#   -ImageAlreadyPublished  verify an existing ECR digest instead of Docker push.
#
# Safety rules enforced before anything is changed:
#   * prepared artifacts unchanged since preparation (hash check);
#   * local image digest == reviewed image_uri; correct AWS account;
#   * no pipeline run in progress: s3://.../pipeline/lock.json must not exist
#     (the control Lambda holds this lock during a run; updating Glue/Lambda
#     mid-run could mix code versions). Checked again right before apply.
#   * the Terraform plan may only: update the curate/score Glue jobs, control
#     Lambda + its policy, configuration object and state machine; create the
#     worker-cancellation EventBridge rule/target/permission and new
#     content-addressed deployment artifacts; and 'forget' (drop from state
#     without deleting in S3) the previous artifact objects - so earlier
#     deployments' artifacts and all run outputs are kept.
# The list of planned changes is written to <PreparedDirectory>/deployment-
# changes.json for review. Without -Execute nothing in AWS is modified.
# =============================================================================
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$PreparedDirectory,
    [Parameter(Mandatory)][string]$Image,
    [string]$Profile = 'is459-g3-tools',
    [switch]$Execute,
    [switch]$ImageAlreadyPublished
)
# Stop at the first error; 'finally' restores the caller's directory.
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Push-Location $projectRoot
try {
    # Re-hash every prepared artifact; reject paths escaping the folder.
    $prepared = (Resolve-Path -LiteralPath $PreparedDirectory).Path
    $config = Get-Content -LiteralPath (Join-Path $prepared 'deployment.json') -Raw | ConvertFrom-Json
    $hashes = Get-Content -LiteralPath (Join-Path $prepared 'artifact-hashes.json') -Raw | ConvertFrom-Json
    foreach ($property in $hashes.PSObject.Properties) {
        $target = [IO.Path]::GetFullPath((Join-Path $prepared $property.Name))
        if (-not $target.StartsWith($prepared + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) { throw 'Artifact escaped preparation directory' }
        if ((Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash.ToLowerInvariant() -ne $property.Value) { throw "Prepared artifact changed: $($property.Name)" }
    }
    # Docker Desktop Linux engine; local image digest must match image_uri.
    $dockerArgs = @('--host', 'npipe:////./pipe/dockerDesktopLinuxEngine')
    $descriptor = & docker @dockerArgs image inspect $Image --format '{{json .Descriptor}}' | ConvertFrom-Json
    if ($LASTEXITCODE -ne 0 -or $config.image_uri -ne "588738581042.dkr.ecr.ap-southeast-1.amazonaws.com/is459-g3-training@$($descriptor.digest)") { throw 'Worker image differs from the prepared digest' }
    # Pin AWS profile/region and confirm the account.
    $env:AWS_PROFILE = $Profile
    $env:AWS_REGION = 'ap-southeast-1'
    $env:AWS_DEFAULT_REGION = 'ap-southeast-1'
    $identity = & aws sts get-caller-identity --profile $Profile --output json | ConvertFrom-Json
    if ($LASTEXITCODE -ne 0 -or $identity.Account -ne '588738581042') { throw 'Wrong AWS account or unavailable session' }
    # Run lock check #1: KeyCount 0 means no pipeline/lock.json, i.e. idle.
    $locks = & aws s3api list-objects-v2 --bucket is459-g3-588738581042-ap-southeast-1 --prefix pipeline/lock.json --max-keys 1 --profile $Profile --query KeyCount --output text
    if ($LASTEXITCODE -ne 0 -or $locks.Trim() -ne '0') { throw 'Pipeline is locked; preserve the current deployment' }
    # An update needs the existing shared state; a fresh account must use
    # deploy-ingestion.ps1 instead.
    $terraform = Join-Path $projectRoot 'output/tools/terraform/terraform.exe'
    $privateRoot = Join-Path $env:LOCALAPPDATA 'IS459-G3/terraform-ingestion'
    & (Join-Path $PSScriptRoot 'init-terraform.ps1') -Stack ingestion -Profile $Profile -Terraform $terraform
    $planPath = Join-Path $privateRoot 'optimisation.tfplan'
    # A changed immutable prefix must not delete the predecessor's S3 objects.
    # Back up private state and forget only allowlisted predecessor uploads;
    # Terraform then plans fresh uploads while old releases remain recoverable.
    $retentionPython = Join-Path $projectRoot 'output/ml-venv/Scripts/python.exe'
    & $retentionPython scripts/aws/retain-deployment-artifacts.py --terraform $terraform --stack-directory infra/ingestion --prefix $config.artifact_prefix
    if ($LASTEXITCODE -ne 0) { throw 'Could not safely retain predecessor deployment artifacts' }
    # Validate the configuration, then save a plan with the new tfvars.
    & $terraform '-chdir=infra/ingestion' validate
    if ($LASTEXITCODE -ne 0) { throw 'Terraform validation failed' }
    & $terraform '-chdir=infra/ingestion' plan "-var-file=$($prepared.Replace('\','/'))/terraform.tfvars.json" "-out=$($planPath.Replace('\','/'))" '-no-color'
    if ($LASTEXITCODE -ne 0) { throw 'Terraform update plan failed' }
    $plan = & $terraform '-chdir=infra/ingestion' show '-json' $planPath | ConvertFrom-Json
    if ($LASTEXITCODE -ne 0) { throw 'Cannot inspect the saved plan' }
    # Allowlist every planned change by address and action. Anything else
    # (a delete, a replace, an unexpected resource) aborts the update.
    $changes = @($plan.resource_changes | Where-Object { ($_.change.actions -join ',') -ne 'no-op' })
    $allowedUpdates = @('aws_glue_job.job["curate"]', 'aws_glue_job.job["score"]', 'aws_glue_job.job["baseline"]', 'aws_glue_job.job["export"]', 'aws_iam_role.glue', 'aws_iam_role_policy.glue', 'aws_iam_role_policy.workflow', 'aws_iam_role_policy.control', 'aws_lambda_function.control', 'aws_s3_object.configuration', 'aws_sfn_state_machine.ingest')
    $allowedCreates = @('aws_cloudwatch_event_rule.cancel_workers', 'aws_cloudwatch_event_target.cancel_workers', 'aws_lambda_permission.cancel_workers', 'aws_glue_job.job["baseline"]', 'aws_glue_job.job["export"]', 'aws_cloudwatch_log_group.glue["baseline/error"]', 'aws_cloudwatch_log_group.glue["baseline/output"]', 'aws_cloudwatch_log_group.glue["export/error"]', 'aws_cloudwatch_log_group.glue["export/output"]')
    # Only obsolete Processing IAM resources may be deleted; never data/jobs.
    $allowedDeletes = @('aws_iam_role_policy.processing', 'aws_iam_role.processing')
    foreach ($change in $changes) {
        $action = $change.change.actions -join ','
        $address = $change.address
        $valid = ($action -eq 'update' -and $address -in $allowedUpdates) -or
            ($action -eq 'create' -and ($address -in $allowedCreates -or $address.StartsWith('aws_s3_object.deployment_artifact['))) -or
            ($action -eq 'delete' -and $address -in $allowedDeletes) -or
            ($action -eq 'forget' -and $address.StartsWith('aws_s3_object.artifact['))
        if (-not $valid) { throw "Unreviewed or destructive change: $address $action" }
    }
    # Save a reviewable summary of the planned changes beside the artifacts.
    $changes | ForEach-Object { [pscustomobject]@{ address = $_.address; actions = $_.change.actions } } |
        ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $prepared 'deployment-changes.json') -Encoding utf8
    if (-not $Execute) { Write-Output 'Validated update plan; no AWS changes or paid jobs started.'; return }
    # -Execute: log Docker in to ECR (stdin password, cleared afterwards), tag
    # as 'optimised-<12 digest chars>', push, and verify the remote digest.
    if (-not $ImageAlreadyPublished) {
        $ecrPassword = & aws ecr get-login-password --profile $Profile --region ap-southeast-1
        if ($LASTEXITCODE -ne 0) { throw 'ECR authentication failed' }
        $ecrPassword | & docker @dockerArgs login --username AWS --password-stdin 588738581042.dkr.ecr.ap-southeast-1.amazonaws.com
        $ecrPassword = $null
        if ($LASTEXITCODE -ne 0) { throw 'Docker authentication failed' }
        $tagName = 'optimised-' + $descriptor.digest.Substring(7, 12)
        $tag = '588738581042.dkr.ecr.ap-southeast-1.amazonaws.com/is459-g3-training:' + $tagName
        & docker @dockerArgs tag $Image $tag
        if ($LASTEXITCODE -ne 0) { throw 'Image tagging failed' }
        & docker @dockerArgs push $tag
        if ($LASTEXITCODE -ne 0) { throw 'Image push failed' }
    }
    # API-uploaded images need no Docker credential store; digest verification remains mandatory.
    $remote = & aws ecr describe-images --profile $Profile --region ap-southeast-1 --repository-name is459-g3-training --image-ids "imageDigest=$($descriptor.digest)" --query 'imageDetails[0].imageDigest' --output text
    if ($LASTEXITCODE -ne 0 -or $remote.Trim() -ne $descriptor.digest) { throw 'Remote worker digest differs' }
    # Run lock check #2: a run may have started while the image was pushing.
    $locks = & aws s3api list-objects-v2 --bucket is459-g3-588738581042-ap-southeast-1 --prefix pipeline/lock.json --max-keys 1 --profile $Profile --query KeyCount --output text
    if ($LASTEXITCODE -ne 0 -or $locks.Trim() -ne '0') { throw 'A run claimed the pipeline during preparation; do not apply this plan' }
    # Apply exactly the saved, allowlisted plan.
    & $terraform '-chdir=infra/ingestion' apply $planPath '-no-color'
    if ($LASTEXITCODE -ne 0) { throw 'Update failed; inspect the retained private state before recovery' }
    Write-Output 'Optimised pipeline deployed; predecessor artifacts and run outputs retained. No batch jobs started by this helper.'
} finally { Pop-Location }
