# Connect a stack to its existing shared state. Does not migrate or apply.
[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [ValidateSet('foundation', 'hosting', 'scoring', 'sagemaker', 'ingestion',
        'cloud-ingestion', 'orchestration', 'streaming', 'dashboard-status',
        'dashboard-control', 'eventbridge-trigger', 'tfstate-bootstrap')]
    [string]$Stack,
    [string]$Profile = 'is459-g3-tools',
    [string]$Terraform = 'terraform',
    [switch]$AllowEmptyState
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$module = if ($Stack -eq 'foundation') { 'terraform' } else { $Stack }
$env:AWS_PROFILE = $Profile
$env:AWS_REGION = 'ap-southeast-1'
$env:AWS_DEFAULT_REGION = 'ap-southeast-1'
if ($env:TF_WORKSPACE -and $env:TF_WORKSPACE -ne 'default') {
    throw 'Only the default workspace is supported for these shared state keys.'
}
$account = & aws sts get-caller-identity --profile $Profile --region ap-southeast-1 --query Account --output text --no-cli-pager
if ($LASTEXITCODE -ne 0 -or $account.Trim() -ne '588738581042') {
    throw 'AWS session unavailable or account differs from Group 3.'
}
$key = "$Stack/terraform.tfstate"
if (-not $AllowEmptyState) {
    & aws s3api head-object --bucket is459-g3-tfstate-588738581042 --key $key --profile $Profile --region ap-southeast-1 --query ContentLength --output text --no-cli-pager | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "Cannot read existing shared state $key. Stop before planning against empty state."
    }
}
# A separate cache prevents an old local-backend initialization being reused.
$env:TF_DATA_DIR = Join-Path $env:LOCALAPPDATA "IS459-G3/terraform-$Stack/shared-runtime"
[IO.Directory]::CreateDirectory($env:TF_DATA_DIR) | Out-Null
$stackDirectory = Join-Path $projectRoot "infra/$module"
$backendConfiguration = Join-Path $projectRoot 'infra/shared.s3.tfbackend'
& $Terraform "-chdir=$stackDirectory" init -input=false -reconfigure "-backend-config=$backendConfiguration" "-backend-config=key=$key"
if ($LASTEXITCODE -ne 0) { throw 'Shared Terraform backend initialization failed.' }
$workspace = & $Terraform "-chdir=$stackDirectory" workspace show
if ($LASTEXITCODE -ne 0 -or $workspace.Trim() -ne 'default') {
    throw 'Unexpected Terraform workspace. Stop before planning.'
}
Write-Host "Connected $Stack to s3://is459-g3-tfstate-588738581042/$key. No apply performed."
