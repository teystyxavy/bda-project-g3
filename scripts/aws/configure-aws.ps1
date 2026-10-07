# =============================================================================
# configure-aws.ps1 - one-time AWS sign-in and project settings (Windows)
# -----------------------------------------------------------------------------
# Purpose: first step for a team member before any Terraform or pipeline
# script. It signs the user in to AWS with the AWS CLI v2, creates a second
# "<ProfileName>-tools" profile that the other scripts and Terraform use, checks
# the account ID, and saves non-secret project settings to
#   %LOCALAPPDATA%\IS459-G3\batch.tfvars.json
# (account, region, project name, alert e-mail, monthly budget USD 10), which
# plan-aws.ps1 later passes to Terraform (infra/terraform).
#
# Security design: the script never asks for or stores passwords, MFA codes or
# access keys. Login happens in the browser ("aws login") or via IAM Identity
# Center SSO; the CLI keeps short-lived credentials in the user's .aws folder.
# The -tools profile obtains them on demand through credential_process
# ("aws configure export-credentials"), so tools that do not understand the
# new login methods (Terraform, boto3) still work.
#
# It deploys nothing. Usage:
#   .\scripts\aws\configure-aws.ps1                 # browser login
#   .\scripts\aws\configure-aws.ps1 -LoginMethod SSO
# =============================================================================
param(
    [ValidateSet('Browser', 'SSO')]
    [string]$LoginMethod = 'Browser',
    [ValidatePattern('^[a-zA-Z0-9][a-zA-Z0-9_-]*$')]
    [string]$ProfileName = 'is459-g3',
    [ValidatePattern('^[a-z]{2}(-[a-z]+)+-[0-9]+$')]
    [string]$Region = 'ap-southeast-1'
)

# Stop on the first error instead of continuing with a half-configured profile.
$ErrorActionPreference = 'Stop'
# Locate aws.exe on PATH, else try the default MSI install location.
$awsCommand = Get-Command aws -CommandType Application -ErrorAction SilentlyContinue
$awsExecutable = if ($awsCommand) { $awsCommand.Source } else { 'C:\Program Files\Amazon\AWSCLIV2\aws.exe' }
if (-not (Test-Path -LiteralPath $awsExecutable)) {
    throw 'Install AWS CLI v2 from https://awscli.amazonaws.com/AWSCLIV2.msi, reopen PowerShell, then run this script again.'
}

# Environment-variable credentials override profiles silently. Refuse to run
# if any are set, so the new profile is not configured against the wrong
# identity (e.g. keys injected by another tool).
foreach ($variableName in @('AWS_ACCESS_KEY_ID', 'AWS_SECRET_ACCESS_KEY', 'AWS_SESSION_TOKEN')) {
    if ([Environment]::GetEnvironmentVariable($variableName)) {
        throw "$variableName is already set. Use a fresh terminal without injected credentials for this profile setup."
    }
}

# Helper: run aws.exe with the given arguments and turn a non-zero exit code
# into a terminating error (external programs do not throw by themselves).
# Its stdout is returned to the caller, e.g. the account ID below.
function Invoke-AwsSetup {
    param([string[]]$Arguments)
    & $awsExecutable @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw 'AWS setup failed. Check the AWS message above; no deployment was attempted.'
    }
}

# Sign in. 'aws login' (browser-based console credentials) only exists in
# AWS CLI 2.32.0+, so the version is checked first; SSO uses the classic
# 'aws configure sso' wizard.
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

# Create '<ProfileName>-tools': a profile whose credentials come from
# credential_process, which asks the CLI to export the logged-in profile's
# temporary credentials. aws.exe's folder is put on PATH so that
# 'aws.exe' in the credential_process command can be found.
$toolsProfile = "$ProfileName-tools"
$env:PATH = "$(Split-Path -Parent $awsExecutable);$env:PATH"
$credentialProcess = 'aws.exe configure export-credentials --profile {0} --format process --region {1}' -f $ProfileName, $Region
Invoke-AwsSetup -Arguments @('configure', 'set', 'credential_process', $credentialProcess, '--profile', $toolsProfile)
Invoke-AwsSetup -Arguments @('configure', 'set', 'region', $Region, '--profile', $toolsProfile)
# Prove the tools profile works and capture the 12-digit account ID.
$accountId = (Invoke-AwsSetup -Arguments @('sts', 'get-caller-identity', '--profile', $toolsProfile, '--query', 'Account', '--output', 'text', '--no-cli-pager')).Trim()
if ($accountId -notmatch '^\d{12}$') {
    throw 'AWS did not return a valid account ID.'
}

# Project settings live outside the repo (per Windows user) so they are never
# committed. If they already exist they must match this account/region;
# otherwise ask for the alert e-mail (used for budget and failure alerts)
# and write a new file with a USD 10 monthly budget.
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
    # UTF-8 without BOM: Terraform's JSON parser rejects a byte-order mark.
    [System.IO.File]::WriteAllText($variablesPath, $settingsJson, [System.Text.UTF8Encoding]::new($false))
}

# Make the tools profile the default for the rest of this PowerShell session.
$env:AWS_PROFILE = $toolsProfile
$env:AWS_REGION = $Region
Write-Host "Ready: account $accountId, region $Region, Terraform profile $toolsProfile"
Write-Host "Project settings: $variablesPath"
Write-Host 'AWS manages your login under your Windows user .aws folder. No credentials are stored in the project.'
Write-Host 'No infrastructure has been deployed. Tell Codex the profile name above when ready to prepare a plan.'
