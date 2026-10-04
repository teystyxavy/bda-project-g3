param(
    [Parameter(Mandatory)]
    [ValidatePattern('^\d{12}$')]
    [string]$AccountId,
    [Parameter(Mandatory)]
    [ValidatePattern('^[^@\s]+@[^@\s]+\.[^@\s]+$')]
    [string]$AlertEmail,
    [string]$ProfileName = 'is459-g3-tools',
    [string]$Region = 'ap-southeast-1',
    [ValidateRange(0.01, 250)]
    [decimal]$MonthlyBudgetUsd = 5
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$terraformCommand = Get-Command terraform -CommandType Application -ErrorAction SilentlyContinue
$terraformExecutable = if ($terraformCommand) { $terraformCommand.Source } else { Join-Path $projectRoot 'output/tools/terraform/terraform.exe' }
$awsCommand = Get-Command aws -CommandType Application -ErrorAction SilentlyContinue
$awsExecutable = if ($awsCommand) { $awsCommand.Source } else { 'C:\Program Files\Amazon\AWSCLIV2\aws.exe' }
foreach ($executable in @($terraformExecutable, $awsExecutable)) {
    if (-not (Test-Path -LiteralPath $executable)) { throw "Required tool not found: $executable" }
}
foreach ($variableName in @('AWS_ACCESS_KEY_ID', 'AWS_SECRET_ACCESS_KEY', 'AWS_SESSION_TOKEN')) {
    if ([Environment]::GetEnvironmentVariable($variableName)) {
        throw "$variableName is set. Use a terminal without injected credentials so Terraform uses the selected profile."
    }
}

$actualAccount = & $awsExecutable sts get-caller-identity --profile $ProfileName --query Account --output text --no-cli-pager
if ($LASTEXITCODE -ne 0 -or $actualAccount.Trim() -ne $AccountId) {
    throw 'AWS login failed or the authenticated account differs from the requested account.'
}

$settingsDirectory = Join-Path $env:LOCALAPPDATA 'IS459-G3'
$variablesPath = Join-Path $settingsDirectory 'batch.tfvars.json'
New-Item -ItemType Directory -Force -Path $settingsDirectory | Out-Null
if (Test-Path -LiteralPath $variablesPath) {
    $settings = Get-Content -LiteralPath $variablesPath -Raw | ConvertFrom-Json
    if ($settings.aws_account_id -ne $AccountId -or $settings.aws_region -ne $Region) {
        throw 'Existing project settings use a different account or region. Review the existing state before switching.'
    }
    $settings.alert_email = $AlertEmail
    if ($PSBoundParameters.ContainsKey('MonthlyBudgetUsd')) {
        $settings.monthly_budget_usd = $MonthlyBudgetUsd
    }
} else {
    $settings = [ordered]@{
        aws_account_id = $AccountId
        aws_region = $Region
        project_name = 'is459-g3'
        alert_email = $AlertEmail
        monthly_budget_usd = $MonthlyBudgetUsd
    }
}
[System.IO.File]::WriteAllText($variablesPath, ($settings | ConvertTo-Json), [System.Text.UTF8Encoding]::new($false))
$env:AWS_PROFILE = $ProfileName
$env:AWS_REGION = $Region
$env:PATH = "$(Split-Path -Parent $awsExecutable);$env:PATH"
$terraformDirectory = Join-Path $projectRoot 'infra/terraform'
& $terraformExecutable "-chdir=$terraformDirectory" init -input=false -reconfigure "-backend-config=path=$settingsDirectory/terraform.tfstate"
if ($LASTEXITCODE -ne 0) { throw 'Terraform initialization failed.' }
& $terraformExecutable "-chdir=$terraformDirectory" plan -input=false -no-color -detailed-exitcode "-var-file=$variablesPath" "-out=$settingsDirectory/batch.tfplan"
if ($LASTEXITCODE -notin @(0, 2)) { throw 'Terraform planning failed; do not apply an older saved plan.' }
Write-Host "Reviewable plan saved to $settingsDirectory/batch.tfplan. No apply was performed."
