# =============================================================================
# deploy-cloud-ingestion.ps1 - deploy the cloud acquisition stack (Lambda image)
# -----------------------------------------------------------------------------
# Purpose: deploys infra/cloud-ingestion, which lets the pipeline download the
# Kaggle data and FX rates *inside AWS* instead of on a laptop. The stack has
# nine resources: an ECR repository + repository policy, a CloudWatch log
# group, the is459-g3-acquire Lambda (container image) with its IAM role and
# policy, and the is459-g3-cloud-ingest Step Functions wrapper with its role
# and policy.
#
# Inputs:
#   -Prepared  folder made by scripts/aws/prepare-cloud-ingestion.py (Docker
#              build context + build-context.json hashes + terraform.tfvars.json)
#   -Image     the locally built Docker image tag of that context
#   -Profile   AWS CLI profile (from configure-aws.ps1)
#   -Execute   without it, the script only verifies integrity and exits.
#
# Why the ordering below: a Lambda container function can only be created
# once its image exists in ECR, but the ECR repository is itself part of the
# stack. So the script (1) plans all nine additions to check them, (2) applies
# only the two ECR resources (-target), (3) pushes the image and checks the
# pushed digest, then (4) plans and applies the remaining seven resources.
# Each plan must contain only 'create' actions with the exact expected count;
# anything else (e.g. a replace or delete) stops the deployment.
#
# Terraform state is shared in the private, locked S3 backend. It starts no acquisition run and no paid batch job.
#
# Usage:
#   .\scripts\aws\deploy-cloud-ingestion.ps1             # verify only
#   .\scripts\aws\deploy-cloud-ingestion.ps1 -Execute    # deploy
# =============================================================================
[CmdletBinding()]
param([string]$Prepared = 'output/cloud-acquisition-context-20261006',
      [string]$Image = 'is459-g3-acquisition:local-20261006',
      [string]$Profile = 'is459-g3-tools', [switch]$Execute)
# Stop at the first error; Push-Location/finally restores the caller's folder.
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Push-Location $projectRoot
try {
    # 1) Integrity: every file in the prepared build context must still have the
    #    SHA-256 recorded by prepare-cloud-ingestion.py.
    $preparedRoot = (Resolve-Path -LiteralPath $Prepared).Path
    $hashes = Get-Content (Join-Path $preparedRoot 'build-context.json') -Raw | ConvertFrom-Json
    foreach ($property in $hashes.PSObject.Properties) {
        $file = Join-Path $preparedRoot $property.Name
        if ((Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash.ToLowerInvariant() -ne $property.Value) { throw "Changed artifact: $($property.Name)" }
    }
    # 2) The Terraform variables must reference the image by the *digest* of
    #    the local image, so Lambda runs exactly the image that was reviewed.
    $variablesFile = Join-Path $preparedRoot 'terraform.tfvars.json'
    $variables = Get-Content $variablesFile -Raw | ConvertFrom-Json
    $descriptor = & docker image inspect $Image --format '{{json .Descriptor}}' | ConvertFrom-Json
    if ($LASTEXITCODE -ne 0 -or $variables.image_uri -ne "588738581042.dkr.ecr.ap-southeast-1.amazonaws.com/is459-g3-acquisition@$($descriptor.digest)") { throw 'Image/config digest mismatch' }
    # Safe default: stop here unless -Execute was given explicitly.
    if (-not $Execute) { Write-Output 'Integrity verified. No cloud writes. -Execute requires approval of the nine additions and image push.'; return }
    # From here on AWS is touched: pin profile/region and verify the account.
    $env:AWS_PROFILE = $Profile
    $env:AWS_REGION = 'ap-southeast-1'
    $env:AWS_DEFAULT_REGION = 'ap-southeast-1'
    $env:AWS_SDK_UA_APP_ID = 'AWSSkill-SageMaker'
    $account = & aws sts get-caller-identity --query Account --output text --profile $Profile
    if ($LASTEXITCODE -ne 0 -or $account.Trim() -ne '588738581042') { throw 'Wrong/unavailable AWS account' }
    # Private Terraform working folders; shared state is initialized below.
    $privateRoot = Join-Path $env:LOCALAPPDATA 'IS459-G3/terraform-cloud-ingestion'
    $env:TF_DATA_DIR = Join-Path $privateRoot 'runtime'
    $terraform = Join-Path $projectRoot 'output/tools/terraform/terraform.exe'
    & (Join-Path $PSScriptRoot 'init-terraform.ps1') -Stack 'cloud-ingestion' -Profile $Profile -Terraform $terraform -AllowEmptyState

    # Helper: save a plan (optionally -target'ed), read it back as JSON and
    # require exactly $Count changes, all of them 'create'. Returns plan path.
    function New-CheckedPlan([string]$Name, [int]$Count, [string[]]$Targets = @()) {
        $planPath = Join-Path $privateRoot "$Name.tfplan"
        $arguments = @('-chdir=infra/cloud-ingestion','plan',"-var-file=$variablesFile","-out=$planPath") + $Targets
        & $terraform @arguments | Out-Host
        if ($LASTEXITCODE -ne 0) { throw 'Terraform plan failed' }
        $plan = & $terraform '-chdir=infra/cloud-ingestion' show -json $planPath | ConvertFrom-Json
        if ($LASTEXITCODE -ne 0) { throw 'Cannot read plan' }
        $changes = @($plan.resource_changes | Where-Object { ($_.change.actions -join ',') -ne 'no-op' })
        if ($changes.Count -ne $Count -or @($changes | Where-Object { ($_.change.actions -join ',') -ne 'create' }).Count) { throw 'Plan differs from reviewed initial additions; inspect private state before retrying' }
        return $planPath
    }
    # Validate all nine additions before staging the image repository separately.
    $null = New-CheckedPlan 'reviewed' 9
    # Apply only the ECR repository and its Lambda pull policy first.
    $foundation = New-CheckedPlan 'image-foundation' 2 @('-target=aws_ecr_repository.acquisition','-target=aws_ecr_repository_policy.lambda')
    & $terraform '-chdir=infra/cloud-ingestion' apply $foundation
    if ($LASTEXITCODE -ne 0) { throw 'Foundation apply failed; preserve state and inspect' }
    # Log Docker in to ECR; the password is piped via stdin (never on the
    # command line) and the variable is cleared straight after use.
    $ecrPassword = & aws ecr get-login-password --profile $Profile --region ap-southeast-1
    if ($LASTEXITCODE -ne 0) { throw 'ECR login unavailable' }
    $ecrPassword | & docker login --username AWS --password-stdin 588738581042.dkr.ecr.ap-southeast-1.amazonaws.com
    $ecrPassword = $null
    if ($LASTEXITCODE -ne 0) { throw 'Docker login failed' }
    # Tag with the first 12 hex chars of the digest (after 'sha256:') and push.
    $tag = "588738581042.dkr.ecr.ap-southeast-1.amazonaws.com/is459-g3-acquisition:acquire-$($descriptor.digest.Substring(7,12))"
    & docker tag $Image $tag
    if ($LASTEXITCODE -ne 0) { throw 'Image tagging failed' }
    & docker push $tag
    if ($LASTEXITCODE -ne 0) { throw 'Image push failed' }
    # ECR must report the same digest as the local image (no drift in transit).
    $remote = & aws ecr describe-images --profile $Profile --region ap-southeast-1 --repository-name is459-g3-acquisition --image-ids "imageTag=acquire-$($descriptor.digest.Substring(7,12))" --query 'imageDetails[0].imageDigest' --output text
    if ($LASTEXITCODE -ne 0 -or $remote.Trim() -ne $descriptor.digest) { throw 'Remote digest mismatch' }
    # Now the image exists: create the other seven resources (Lambda, roles, workflow).
    $runtime = New-CheckedPlan 'runtime' 7
    & $terraform '-chdir=infra/cloud-ingestion' apply $runtime
    if ($LASTEXITCODE -ne 0) { throw 'Runtime apply failed; preserve state and inspect' }
    Write-Output 'Cloud acquisition deployed. No acquisition or paid batch job started.'
} finally { Pop-Location }
