[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$PreparedDirectory,
    [Parameter(Mandatory)][string]$Image,
    [string]$Profile = 'is459-g3-tools',
    [switch]$Execute
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Push-Location $projectRoot
try {
    $prepared = (Resolve-Path -LiteralPath $PreparedDirectory).Path
    $config = Get-Content -LiteralPath (Join-Path $prepared 'deployment.json') -Raw | ConvertFrom-Json
    $hashes = Get-Content -LiteralPath (Join-Path $prepared 'artifact-hashes.json') -Raw | ConvertFrom-Json
    foreach ($property in $hashes.PSObject.Properties) {
        $target = [IO.Path]::GetFullPath((Join-Path $prepared $property.Name))
        if (-not $target.StartsWith($prepared + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) { throw 'Artifact escaped preparation directory' }
        if ((Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash.ToLowerInvariant() -ne $property.Value) { throw "Prepared artifact changed: $($property.Name)" }
    }
    $dockerArgs = @('--host', 'npipe:////./pipe/dockerDesktopLinuxEngine')
    $descriptor = & docker @dockerArgs image inspect $Image --format '{{json .Descriptor}}' | ConvertFrom-Json
    if ($LASTEXITCODE -ne 0 -or $config.image_uri -ne "588738581042.dkr.ecr.ap-southeast-1.amazonaws.com/is459-g3-training@$($descriptor.digest)") { throw 'Worker image differs from the prepared digest' }
    $env:AWS_PROFILE = $Profile
    $env:AWS_REGION = 'ap-southeast-1'
    $env:AWS_DEFAULT_REGION = 'ap-southeast-1'
    $identity = & aws sts get-caller-identity --profile $Profile --output json | ConvertFrom-Json
    if ($LASTEXITCODE -ne 0 -or $identity.Account -ne '588738581042') { throw 'Wrong AWS account or unavailable session' }
    $locks = & aws s3api list-objects-v2 --bucket is459-g3-588738581042-ap-southeast-1 --prefix pipeline/lock.json --max-keys 1 --profile $Profile --query KeyCount --output text
    if ($LASTEXITCODE -ne 0 -or $locks.Trim() -ne '0') { throw 'Pipeline is locked; preserve the current deployment' }
    $terraform = Join-Path $projectRoot 'output/tools/terraform/terraform.exe'
    $privateRoot = Join-Path $env:LOCALAPPDATA 'IS459-G3/terraform-ingestion'
    if (-not (Test-Path -LiteralPath (Join-Path $privateRoot 'state/terraform.tfstate'))) { throw 'Existing ingestion state is required; use the initial deployment procedure for a fresh account' }
    $env:TF_DATA_DIR = Join-Path $privateRoot 'runtime'
    $planPath = Join-Path $privateRoot 'optimisation.tfplan'
    & $terraform '-chdir=infra/ingestion' validate
    if ($LASTEXITCODE -ne 0) { throw 'Terraform validation failed' }
    & $terraform '-chdir=infra/ingestion' plan "-var-file=$($prepared.Replace('\','/'))/terraform.tfvars.json" "-out=$($planPath.Replace('\','/'))" '-no-color'
    if ($LASTEXITCODE -ne 0) { throw 'Terraform update plan failed' }
    $plan = & $terraform '-chdir=infra/ingestion' show '-json' $planPath | ConvertFrom-Json
    if ($LASTEXITCODE -ne 0) { throw 'Cannot inspect the saved plan' }
    $changes = @($plan.resource_changes | Where-Object { ($_.change.actions -join ',') -ne 'no-op' })
    $allowedUpdates = @('aws_glue_job.job["curate"]', 'aws_glue_job.job["score"]', 'aws_iam_role_policy.control', 'aws_lambda_function.control', 'aws_s3_object.configuration', 'aws_sfn_state_machine.ingest')
    $allowedCreates = @('aws_cloudwatch_event_rule.cancel_workers', 'aws_cloudwatch_event_target.cancel_workers', 'aws_lambda_permission.cancel_workers')
    foreach ($change in $changes) {
        $action = $change.change.actions -join ','
        $address = $change.address
        $valid = ($action -eq 'update' -and $address -in $allowedUpdates) -or
            ($action -eq 'create' -and ($address -in $allowedCreates -or $address.StartsWith('aws_s3_object.deployment_artifact['))) -or
            ($action -eq 'forget' -and $address.StartsWith('aws_s3_object.artifact['))
        if (-not $valid) { throw "Unreviewed or destructive change: $address $action" }
    }
    $changes | ForEach-Object { [pscustomobject]@{ address = $_.address; actions = $_.change.actions } } |
        ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $prepared 'deployment-changes.json') -Encoding utf8
    if (-not $Execute) { Write-Output 'Validated update plan; no AWS changes or paid jobs started.'; return }
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
    $remote = & aws ecr describe-images --profile $Profile --region ap-southeast-1 --repository-name is459-g3-training --image-ids "imageTag=$tagName" --query 'imageDetails[0].imageDigest' --output text
    if ($LASTEXITCODE -ne 0 -or $remote.Trim() -ne $descriptor.digest) { throw 'Remote worker digest differs' }
    $locks = & aws s3api list-objects-v2 --bucket is459-g3-588738581042-ap-southeast-1 --prefix pipeline/lock.json --max-keys 1 --profile $Profile --query KeyCount --output text
    if ($LASTEXITCODE -ne 0 -or $locks.Trim() -ne '0') { throw 'A run claimed the pipeline during preparation; do not apply this plan' }
    & $terraform '-chdir=infra/ingestion' apply $planPath '-no-color'
    if ($LASTEXITCODE -ne 0) { throw 'Update failed; inspect the retained private state before recovery' }
    Write-Output 'Optimised pipeline deployed; predecessor artifacts and run outputs retained. No batch jobs started by this helper.'
} finally { Pop-Location }
