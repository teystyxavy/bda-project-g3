param(
    [ValidateSet('Browser', 'SSO')]
    [string]$LoginMethod = 'Browser',
    [ValidatePattern('^[a-zA-Z0-9][a-zA-Z0-9_-]*$')]
    [string]$ProfileName = 'is459-g3',
    [ValidatePattern('^[a-z]{2}(-[a-z]+)+-[0-9]+$')]
    [string]$Region = 'ap-southeast-1'
)

$ErrorActionPreference = 'Stop'
$awsCommand = Get-Command aws -CommandType Application -ErrorAction SilentlyContinue
$awsExecutable = if ($awsCommand) { $awsCommand.Source } else { 'C:\Program Files\Amazon\AWSCLIV2\aws.exe' }
if (-not (Test-Path -LiteralPath $awsExecutable)) {
    throw 'Install AWS CLI v2 from https://awscli.amazonaws.com/AWSCLIV2.msi, reopen PowerShell, then run this script again.'
}

foreach ($variableName in @('AWS_ACCESS_KEY_ID', 'AWS_SECRET_ACCESS_KEY', 'AWS_SESSION_TOKEN')) {
    if ([Environment]::GetEnvironmentVariable($variableName)) {
        throw "$variableName is already set. Use a fresh terminal without injected credentials for this profile setup."
    }
}

function Invoke-AwsSetup {
    param([string[]]$Arguments)
    & $awsExecutable @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw 'AWS setup failed. Check the AWS message above; no deployment was attempted.'
    }
}

if ($LoginMethod -eq 'Browser') {
    $versionText = & $awsExecutable --version
    if ($versionText -notmatch 'aws-cli/(\d+\.\d+\.\d+)' -or [version]$Matches[1] -lt [version]'2.32.0') {
        throw 'Browser login requires AWS CLI 2.32.0 or newer. Update using https://awscli.amazonaws.com/AWSCLIV2.msi.'
    }
    Write-Host 'Sign in directly on the AWS page that opens. This script never asks for your password or MFA code.'
    Invoke-AwsSetup -Arguments @('login', '--profile', $ProfileName, '--region', $Region)
} else {
    Write-Host 'Use the SSO start URL and SSO region supplied by your organization.'
    Invoke-AwsSetup -Arguments @('configure', 'sso', '--profile', $ProfileName)
}

$toolsProfile = "$ProfileName-tools"
$env:PATH = "$(Split-Path -Parent $awsExecutable);$env:PATH"
$credentialProcess = 'aws.exe configure export-credentials --profile {0} --format process' -f $ProfileName
Invoke-AwsSetup -Arguments @('configure', 'set', 'credential_process', $credentialProcess, '--profile', $toolsProfile)
Invoke-AwsSetup -Arguments @('configure', 'set', 'region', $Region, '--profile', $toolsProfile)
$accountId = (Invoke-AwsSetup -Arguments @('sts', 'get-caller-identity', '--profile', $toolsProfile, '--query', 'Account', '--output', 'text', '--no-cli-pager')).Trim()
if ($accountId -notmatch '^\d{12}$') {
    throw 'AWS did not return a valid account ID.'
}

$settingsDirectory = Join-Path $env:LOCALAPPDATA 'IS459-G3'
$variablesPath = Join-Path $settingsDirectory 'batch.tfvars.json'
if (Test-Path -LiteralPath $variablesPath) {
    $existing = Get-Content -LiteralPath $variablesPath -Raw | ConvertFrom-Json
    if ($existing.aws_account_id -ne $accountId -or $existing.aws_region -ne $Region) {
        throw "Existing settings at $variablesPath use a different account or region. Review them before continuing."
    }
    Write-Host "Keeping existing project settings at $variablesPath"
} else {
    $alertEmail = Read-Host 'Email address for project budget and failure alerts'
    if ($alertEmail -notmatch '^[^@\s]+@[^@\s]+\.[^@\s]+$') {
        throw 'Enter a valid alert email address and rerun the helper. Your AWS login is retained.'
    }
    New-Item -ItemType Directory -Force -Path $settingsDirectory | Out-Null
    $settings = [ordered]@{
        aws_account_id = $accountId
        aws_region = $Region
        project_name = 'is459-g3'
        alert_email = $alertEmail
        monthly_budget_usd = 10
    }
    $settingsJson = $settings | ConvertTo-Json
    [System.IO.File]::WriteAllText($variablesPath, $settingsJson, [System.Text.UTF8Encoding]::new($false))
}

$env:AWS_PROFILE = $toolsProfile
$env:AWS_REGION = $Region
Write-Host "Ready: account $accountId, region $Region, Terraform profile $toolsProfile"
Write-Host "Project settings: $variablesPath"
Write-Host 'AWS manages your login under your Windows user .aws folder. No credentials are stored in the project.'
Write-Host 'No infrastructure has been deployed. Tell Codex the profile name above when ready to prepare a plan.'
