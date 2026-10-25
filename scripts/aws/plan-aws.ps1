# =============================================================================
# plan-aws.ps1 - create a reviewable Terraform plan for the base AWS stack
# -----------------------------------------------------------------------------
# Purpose: after configure-aws.ps1, this script prepares (but never applies) a
# Terraform plan for infra/terraform - the project's foundation resources
# (encrypted, versioned S3 data bucket; Glue catalog database/tables and a
# Glue job; Athena workgroup and named queries; AWS Budget, SNS alert topic
# and failure EventBridge rule; IAM roles).
#
# Steps:
#   1. Find terraform.exe and aws.exe (PATH, or the repo's output/tools copy /
#      the default AWS CLI install path).
#   2. Refuse to run if AWS_* key variables are set, so Terraform uses the
#      selected profile and not stray credentials.
#   3. Check the signed-in account equals -AccountId.
#   4. Create or update %LOCALAPPDATA%\IS459-G3\batch.tfvars.json (account,
#      region, project name, alert e-mail, monthly budget).
#   5. terraform init with a *local* state file in that same private folder
#      (state can contain resource details, so it is kept out of the repo),
#      then terraform plan -out=batch.tfplan.
#
# Reads/writes: AWS STS (read-only), local settings, Terraform state and plan.
# Deploys nothing; a human reviews batch.tfplan and applies it separately.
#
# Usage:
#   .\scripts\aws\plan-aws.ps1 -AccountId 588738581042 -AlertEmail me@example.com
#   .\scripts\aws\plan-aws.ps1 -AccountId 588738581042 -AlertEmail me@example.com -MonthlyBudgetUsd 10
# =============================================================================
param(
    [Parameter(Mandatory)]
    [ValidatePattern('^\d{12}$')]
    [string]$AccountId,
    [Parameter(Mandatory)]
    [ValidatePattern('^[^@\s]+@[^@\s]+\.[^@\s]+$')]
    [string]$AlertEmail,
    [string]$ProfileName = 'default',
    [string]$Region = 'ap-southeast-1',
    [ValidateRange(0.01, 250)]
    [decimal]$MonthlyBudgetUsd = 5
)

# Any error stops the script before Terraform runs.
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
# Prefer tools on PATH; fall back to the project's bundled/default copies.
$terraformCommand = Get-Command terraform -CommandType Application -ErrorAction SilentlyContinue
$terraformExecutable = if ($terraformCommand) { $terraformCommand.Source } else { Join-Path $projectRoot 'output/tools/terraform/terraform.exe' }
$awsCommand = Get-Command aws -CommandType Application -ErrorAction SilentlyContinue
$awsExecutable = if ($awsCommand) { $awsCommand.Source } else { 'C:\Program Files\Amazon\AWSCLIV2\aws.exe' }
foreach ($executable in @($terraformExecutable, $awsExecutable)) {
    if (-not (Test-Path -LiteralPath $executable)) { throw "Required tool not found: $executable" }
}
# Environment-variable keys would override the profile for Terraform.
foreach ($variableName in @('AWS_ACCESS_KEY_ID', 'AWS_SECRET_ACCESS_KEY', 'AWS_SESSION_TOKEN')) {
    if ([Environment]::GetEnvironmentVariable($variableName)) {
        throw "$variableName is set. Use a terminal without injected credentials so Terraform uses the selected profile."
    }
}

# Confirm the profile is logged in AND points at the requested account.
$actualAccount = & $awsExecutable sts get-caller-identity --profile $ProfileName --query Account --output text --no-cli-pager
if ($LASTEXITCODE -ne 0 -or $actualAccount.Trim() -ne $AccountId) {
    throw 'AWS login failed or the authenticated account differs from the requested account.'
}

# Terraform variables file. An existing file must match account/region;
# the alert e-mail is always refreshed and the budget only when the
# -MonthlyBudgetUsd parameter was explicitly passed.
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
# Write as UTF-8 without BOM (Terraform rejects a BOM in JSON).
[System.IO.File]::WriteAllText($variablesPath, ($settings | ConvertTo-Json), [System.Text.UTF8Encoding]::new($false))
$env:AWS_PROFILE = $ProfileName
$env:AWS_REGION = $Region
$env:PATH = "$(Split-Path -Parent $awsExecutable);$env:PATH"
# init: -reconfigure picks up the local backend path; -input=false prevents
# interactive prompts. The state file is kept in the private settings folder.
$terraformDirectory = Join-Path $projectRoot 'infra/terraform'
& $terraformExecutable "-chdir=$terraformDirectory" init -input=false -reconfigure "-backend-config=path=$settingsDirectory/terraform.tfstate"
if ($LASTEXITCODE -ne 0) { throw 'Terraform initialization failed.' }
# plan with -detailed-exitcode: 0 = no changes, 2 = changes present (both
# fine), 1 = error. The plan is saved so exactly what was reviewed is applied.
& $terraformExecutable "-chdir=$terraformDirectory" plan -input=false -no-color -detailed-exitcode "-var-file=$variablesPath" "-out=$settingsDirectory/batch.tfplan"
if ($LASTEXITCODE -notin @(0, 2)) { throw 'Terraform planning failed; do not apply an older saved plan.' }
Write-Host "Reviewable plan saved to $settingsDirectory/batch.tfplan. No apply was performed."
