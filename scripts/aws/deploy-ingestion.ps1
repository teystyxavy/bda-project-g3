# =============================================================================
# deploy-ingestion.ps1 - first-time deployment of the batch ingestion pipeline
# -----------------------------------------------------------------------------
# Purpose: deploys the two Terraform stacks the main batch pipeline needs, in
# order, into a fresh account:
#   1. infra/sagemaker  (3 resources): ECR repository is459-g3-training and the
#      SageMaker training IAM role + policy. After applying it, the worker image
#      (built from prepare-sagemaker-container.py's context) is pushed to ECR.
#   2. infra/ingestion  (33 resources at the time of review): S3 artifact
#      uploads and deployment config, the control Lambda, Glue database and
#      curate/score jobs, IAM roles/policies (Glue, Lambda, SageMaker
#      processing, Step Functions), log groups, the is459-g3-ingest Step
#      Functions workflow and the EventBridge rule that cancels workers when
#      an execution fails.
#
# Inputs:
#   -PreparedDirectory  output of scripts/aws/prepare-ingestion.py
#                       (deployment.json, artifact-hashes.json, tfvars, ...)
#   -Image              local Docker image whose digest is pinned in
#                       deployment.json (image_uri = ...is459-g3-training@sha256:...)
#   -Profile            AWS CLI profile;  -Execute  actually deploy.
#
# Safety checks, in order: every prepared artifact still matches its recorded
# hash (and stays inside the folder); the local image digest equals the
# reviewed image_uri; without -Execute it stops there. With -Execute it runs
# scripts/ingestion/check-ingestion.py (read-only preflight) and requires the
# SageMaker instance quotas; each Terraform plan must contain exactly the
# reviewed number of resources, all 'create'. Terraform state is private
# (%LOCALAPPDATA%\IS459-G3\terraform-<module>). No jobs are started; use
# update-ingestion.ps1 for an already deployed stack.
# =============================================================================
[CmdletBinding()]
param(
    [string]$PreparedDirectory = 'output/optimisation-artifacts-20261006',
    [string]$Image = 'is459-g3-training:optimised-20261006',
    [string]$Profile = 'is459-g3-tools',
    [switch]$Execute
)
# Stop at the first error; 'finally' restores the caller's directory.
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Push-Location $projectRoot
try {
    # Integrity check of every prepared artifact. GetFullPath + StartsWith
    # rejects names like '..\x' that would escape the prepared folder.
    $prepared = (Resolve-Path -LiteralPath $PreparedDirectory).Path
    $config = Get-Content -LiteralPath (Join-Path $prepared 'deployment.json') -Raw | ConvertFrom-Json
    $hashes = Get-Content -LiteralPath (Join-Path $prepared 'artifact-hashes.json') -Raw | ConvertFrom-Json
    foreach ($property in $hashes.PSObject.Properties) {
        $target = [IO.Path]::GetFullPath((Join-Path $prepared $property.Name))
        if (-not $target.StartsWith($prepared + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) { throw 'Artifact path outside preparation directory' }
        if ((Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash.ToLowerInvariant() -ne $property.Value) { throw "Artifact changed: $($property.Name)" }
    }
    # Talk to Docker Desktop's Linux engine explicitly (Windows named pipe), then
    # require the local image digest to equal the reviewed image_uri.
    $dockerArgs = @('--host', 'npipe:////./pipe/dockerDesktopLinuxEngine')
    $descriptor = & docker @dockerArgs image inspect $Image --format '{{json .Descriptor}}' | ConvertFrom-Json
    if ($LASTEXITCODE -ne 0 -or $config.image_uri -ne "588738581042.dkr.ecr.ap-southeast-1.amazonaws.com/is459-g3-training@$($descriptor.digest)") { throw 'Local image differs from reviewed digest' }
    if (-not $Execute) {
        Write-Output 'Integrity checks passed. No deployment performed. Read planning/runbooks/pipeline-optimisation-implementation.md; use update-ingestion.ps1 for an existing stack.'
        return
    }
    # -Execute path: pin AWS profile/region for every child process.
    $env:AWS_PROFILE = $Profile
    $env:AWS_REGION = 'ap-southeast-1'
    $env:AWS_DEFAULT_REGION = 'ap-southeast-1'
    $env:AWS_SDK_UA_APP_ID = 'AWSSkill-SageMaker'
    # Read-only preflight (account, buckets, Athena guard, quotas, prices, ASL);
    # stop if the ml.m5 processing/training quotas are not available.
    $python = Join-Path $projectRoot 'output/ml-venv/Scripts/python.exe'
    & $python scripts/ingestion/check-ingestion.py --profile $Profile --output output/ingestion-deployment-preflight.json
    if ($LASTEXITCODE -ne 0) { throw 'AWS preflight failed' }
    $preflight = Get-Content output/ingestion-deployment-preflight.json -Raw | ConvertFrom-Json
    if (-not $preflight.quota_ready) { throw 'Required SageMaker quota unavailable' }
    $terraform = Join-Path $projectRoot 'output/tools/terraform/terraform.exe'
    # Deploy sagemaker first (creates the ECR repo the image is pushed to), then
    # ingestion (which references that image). Each module gets its own
    # private data dir and local state file.
    foreach ($module in @('sagemaker', 'ingestion')) {
        $privateRoot = Join-Path $env:LOCALAPPDATA "IS459-G3/terraform-$module"
        $env:TF_DATA_DIR = Join-Path $privateRoot 'runtime'
        $statePath = Join-Path $privateRoot 'state/terraform.tfstate'
        [IO.Directory]::CreateDirectory((Split-Path $statePath -Parent)) > $null
        $initArgs = @("-chdir=infra/$module", 'init', "-backend-config=path=$($statePath.Replace('\','/'))")
        $providerMirror = Join-Path $env:LOCALAPPDATA 'IS459-G3/terraform-orchestration/providers'
        if (Test-Path -LiteralPath $providerMirror) { $initArgs += "-plugin-dir=$($providerMirror.Replace('\','/'))" }
        & $terraform @initArgs
        if ($LASTEXITCODE -ne 0) { throw 'Terraform initialization failed' }
        # Save a plan; only the ingestion module needs the prepared tfvars file.
        $planArgs = @("-chdir=infra/$module", 'plan', "-out=$($privateRoot.Replace('\','/'))/deployment.tfplan")
        if ($module -eq 'ingestion') { $planArgs += "-var-file=$($prepared.Replace('\','/'))/terraform.tfvars.json" }
        & $terraform @planArgs
        if ($LASTEXITCODE -ne 0) { throw 'Terraform plan failed' }
        $plan = & $terraform "-chdir=infra/$module" show -json "$privateRoot/deployment.tfplan" | ConvertFrom-Json
        if ($LASTEXITCODE -ne 0) { throw 'Cannot inspect deployment plan' }
        # Reviewed resource counts: 3 for sagemaker, 33 for ingestion. Any other
        # count, or any non-'create' action, means the plan is not the reviewed one.
        $expected = if ($module -eq 'sagemaker') { 3 } else { 33 }
        $changes = @($plan.resource_changes | Where-Object { ($_.change.actions -join ',') -ne 'no-op' })
        if ($changes.Count -ne $expected -or @($changes | Where-Object { ($_.change.actions -join ',') -ne 'create' }).Count) { throw 'Plan differs from the reviewed additions; stop and review it' }
        # Apply exactly the saved, checked plan.
        & $terraform "-chdir=infra/$module" apply "$privateRoot/deployment.tfplan"
        if ($LASTEXITCODE -ne 0) { throw 'Terraform deployment failed; inspect private state before resuming' }
        # Once the ECR repo exists: log in (password via stdin, then cleared),
        # tag with 'ingest-<12 digest chars>', push, and confirm ECR reports the
        # same digest that deployment.json pins.
        if ($module -eq 'sagemaker') {
            $ecrPassword = & aws ecr get-login-password --profile $Profile --region ap-southeast-1
            if ($LASTEXITCODE -ne 0) { throw 'ECR authentication failed' }
            $ecrPassword | & docker @dockerArgs login --username AWS --password-stdin 588738581042.dkr.ecr.ap-southeast-1.amazonaws.com
            $ecrPassword = $null
            if ($LASTEXITCODE -ne 0) { throw 'Docker ECR login failed' }
            $tag = "588738581042.dkr.ecr.ap-southeast-1.amazonaws.com/is459-g3-training:ingest-$($descriptor.digest.Substring(7,12))"
            & docker @dockerArgs tag $Image $tag
            if ($LASTEXITCODE -ne 0) { throw 'Docker tagging failed' }
            & docker @dockerArgs push $tag
            if ($LASTEXITCODE -ne 0) { throw 'Image push failed' }
            $remoteDigest = & aws ecr describe-images --profile $Profile --region ap-southeast-1 --repository-name is459-g3-training --image-ids "imageTag=ingest-$($descriptor.digest.Substring(7,12))" --query 'imageDetails[0].imageDigest' --output text
            if ($LASTEXITCODE -ne 0 -or $remoteDigest.Trim() -ne $descriptor.digest) { throw 'Remote image digest differs' }
        }
    }
    Write-Output 'Deployed. No processing or training job started. Run scripts/ingestion/ingest.ps1 -Rows 1000 for the reviewed cloud smoke test.'
} finally { Pop-Location }
