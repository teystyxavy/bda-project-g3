[CmdletBinding()]
param([string]$Prepared = 'output/cloud-acquisition-context-20261006',
      [string]$Image = 'is459-g3-acquisition:local-20261006',
      [string]$Profile = 'is459-g3-tools', [switch]$Execute)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Push-Location $projectRoot
try {
    $preparedRoot = (Resolve-Path -LiteralPath $Prepared).Path
    $hashes = Get-Content (Join-Path $preparedRoot 'build-context.json') -Raw | ConvertFrom-Json
    foreach ($property in $hashes.PSObject.Properties) {
        $file = Join-Path $preparedRoot $property.Name
        if ((Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash.ToLowerInvariant() -ne $property.Value) { throw "Changed artifact: $($property.Name)" }
    }
    $variablesFile = Join-Path $preparedRoot 'terraform.tfvars.json'
    $variables = Get-Content $variablesFile -Raw | ConvertFrom-Json
    $descriptor = & docker image inspect $Image --format '{{json .Descriptor}}' | ConvertFrom-Json
    if ($LASTEXITCODE -ne 0 -or $variables.image_uri -ne "588738581042.dkr.ecr.ap-southeast-1.amazonaws.com/is459-g3-acquisition@$($descriptor.digest)") { throw 'Image/config digest mismatch' }
    if (-not $Execute) { Write-Output 'Integrity verified. No cloud writes. -Execute requires approval of the nine additions and image push.'; return }
    $env:AWS_PROFILE = $Profile
    $env:AWS_REGION = 'ap-southeast-1'
    $env:AWS_DEFAULT_REGION = 'ap-southeast-1'
    $env:AWS_SDK_UA_APP_ID = 'AWSSkill-SageMaker'
    $account = & aws sts get-caller-identity --query Account --output text --profile $Profile
    if ($LASTEXITCODE -ne 0 -or $account.Trim() -ne '588738581042') { throw 'Wrong/unavailable AWS account' }
    $privateRoot = Join-Path $env:LOCALAPPDATA 'IS459-G3/terraform-cloud-ingestion'
    $env:TF_DATA_DIR = Join-Path $privateRoot 'runtime'
    $terraform = Join-Path $projectRoot 'output/tools/terraform/terraform.exe'
    $statePath = Join-Path $privateRoot 'state/terraform.tfstate'
    $providerMirror = Join-Path $env:LOCALAPPDATA 'IS459-G3/terraform-orchestration/providers'
    $initArgs = @('-chdir=infra/cloud-ingestion', 'init', "-backend-config=path=$($statePath.Replace('\','/'))")
    if (Test-Path -LiteralPath $providerMirror) { $initArgs += "-plugin-dir=$($providerMirror.Replace('\','/'))" }
    & $terraform @initArgs
    if ($LASTEXITCODE -ne 0) { throw 'Terraform initialization failed' }
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
    $foundation = New-CheckedPlan 'image-foundation' 2 @('-target=aws_ecr_repository.acquisition','-target=aws_ecr_repository_policy.lambda')
    & $terraform '-chdir=infra/cloud-ingestion' apply $foundation
    if ($LASTEXITCODE -ne 0) { throw 'Foundation apply failed; preserve state and inspect' }
    $ecrPassword = & aws ecr get-login-password --profile $Profile --region ap-southeast-1
    if ($LASTEXITCODE -ne 0) { throw 'ECR login unavailable' }
    $ecrPassword | & docker login --username AWS --password-stdin 588738581042.dkr.ecr.ap-southeast-1.amazonaws.com
    $ecrPassword = $null
    if ($LASTEXITCODE -ne 0) { throw 'Docker login failed' }
    $tag = "588738581042.dkr.ecr.ap-southeast-1.amazonaws.com/is459-g3-acquisition:acquire-$($descriptor.digest.Substring(7,12))"
    & docker tag $Image $tag
    if ($LASTEXITCODE -ne 0) { throw 'Image tagging failed' }
    & docker push $tag
    if ($LASTEXITCODE -ne 0) { throw 'Image push failed' }
    $remote = & aws ecr describe-images --profile $Profile --region ap-southeast-1 --repository-name is459-g3-acquisition --image-ids "imageTag=acquire-$($descriptor.digest.Substring(7,12))" --query 'imageDetails[0].imageDigest' --output text
    if ($LASTEXITCODE -ne 0 -or $remote.Trim() -ne $descriptor.digest) { throw 'Remote digest mismatch' }
    $runtime = New-CheckedPlan 'runtime' 7
    & $terraform '-chdir=infra/cloud-ingestion' apply $runtime
    if ($LASTEXITCODE -ne 0) { throw 'Runtime apply failed; preserve state and inspect' }
    Write-Output 'Cloud acquisition deployed. No acquisition or paid batch job started.'
} finally { Pop-Location }
