<#
.SYNOPSIS
Download selected IBM AML files locally, or run the HI-Small AWS pipeline.
.EXAMPLE
.\scripts\ingestion\ingest.ps1 -DownloadOnly -Variant LI -Size Medium -IncludePatterns -Refresh
.EXAMPLE
.\scripts\ingestion\ingest.ps1 -DownloadOnly -Subset HI-Large -Files Transactions -DryRun
.EXAMPLE
.\scripts\ingestion\ingest.ps1 -LocalAcquisition -Trigger EventBridge -Rows 1000
Upload from this computer and let the S3 upload start the pipeline through EventBridge.
.EXAMPLE
.\scripts\ingestion\ingest.ps1 -Refresh
Refresh HI-Small in AWS and run the pipeline.
#>
[CmdletBinding()]
param(
    [string]$Python = '', [string]$Profile = 'is459-g3-tools',
    [string]$DatasetDirectory = 'dataset', [int]$DatasetVersion = 0, [int]$Rows = 0,
    [ValidateSet('HI-Small', 'HI-Medium', 'HI-Large', 'LI-Small', 'LI-Medium', 'LI-Large')]
    [string]$Subset = '',
    [Alias('Prefix')][ValidateSet('HI', 'LI')][string]$Variant = '',
    [ValidateSet('Small', 'Medium', 'Large')][string]$Size = '',
    [ValidateSet('Transactions', 'Accounts', 'Patterns')][string[]]$Files = @(),
    [switch]$IncludePatterns, [switch]$DownloadOnly, [switch]$DryRun, [switch]$ListOptions,
    [string]$FxFile = '', [string]$Deployment = '', [string]$RunId = '',
    [switch]$PrepareOnly, [switch]$Train, [switch]$Refresh, [switch]$Rerun,
    [switch]$NoWait, [switch]$LocalAcquisition, [ValidateSet('Direct', 'EventBridge')][string]$Trigger = 'Direct',
    [string]$Status = '', [string]$Unlock = ''
)
$ErrorActionPreference = 'Stop'
if (($Status -or $Unlock) -and ($DryRun -or $ListOptions -or $Subset -or $Variant -or $Size -or $Files.Count -or $IncludePatterns)) {
    throw 'Selection, preview and listing options cannot be combined with -Status or -Unlock.'
}
if ($DownloadOnly -and ($PrepareOnly -or $Train -or $Rerun -or $NoWait -or $LocalAcquisition -or
        $FxFile -or $Deployment -or $RunId -or $Status -or $Unlock -or $PSBoundParameters.ContainsKey('Rows'))) {
    throw '-DownloadOnly cannot be combined with pipeline, preparation or status options. Use -DatasetDirectory, -DatasetVersion, -Refresh and file-selection options.'
}
$projectRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
if (-not $Python) {
    $candidate = Join-Path $projectRoot 'output/ml-venv/Scripts/python.exe'
    $Python = if (Test-Path -LiteralPath $candidate) { $candidate } else { 'python' }
}
$env:AWS_REGION = 'ap-southeast-1'
$env:AWS_DEFAULT_REGION = 'ap-southeast-1'
$env:AWS_SDK_UA_APP_ID = 'AWSSkill-SageMaker'
$arguments = @((Join-Path $PSScriptRoot 'ingest.py'), '--profile', $Profile)
if ($Status) { $arguments += @('status', '--run-id', $Status) }
elseif ($Unlock) { $arguments += @('unlock', '--run-id', $Unlock) }
else {
    $arguments += $(if ($DownloadOnly) { 'download' } elseif ($PrepareOnly) { 'prepare' } else { 'run' })
    $arguments += @('--dataset-directory', $DatasetDirectory)
    foreach ($pair in @(@('--subset', $Subset), @('--variant', $Variant), @('--size', $Size))) {
        if ($pair[1]) { $arguments += @($pair[0], $pair[1]) }
    }
    if ($Files.Count) { $arguments += '--files'; $arguments += @($Files | ForEach-Object { $_.ToLowerInvariant() }) }
    if ($IncludePatterns) { $arguments += '--include-patterns' }
    if ($DryRun) { $arguments += '--dry-run' }
    if ($ListOptions) { $arguments += '--list-options' }
    foreach ($pair in @(@('--dataset-version', $DatasetVersion), @('--rows', $Rows),
                         @('--fx-file', $FxFile), @('--deployment', $Deployment), @('--run-id', $RunId))) {
        if ($pair[1] -or ($pair[0] -eq '--dataset-version' -and $PSBoundParameters.ContainsKey('DatasetVersion')) -or
            ($pair[0] -eq '--rows' -and $PSBoundParameters.ContainsKey('Rows'))) {
            $arguments += @($pair[0], [string]$pair[1])
        }
    }
    if ($Train) { $arguments += '--train' }
    if ($Refresh) { $arguments += '--refresh' }
    if ($Rerun) { $arguments += '--rerun' }
    if ($NoWait -and -not $PrepareOnly) { $arguments += '--no-wait' }
    if ($LocalAcquisition -and -not $PrepareOnly) { $arguments += '--local-acquisition' }
    if ($Trigger -eq 'EventBridge' -and -not $PrepareOnly) { $arguments += @('--trigger', 'eventbridge') }
}
Push-Location $projectRoot
try { & $Python @arguments; if ($LASTEXITCODE -ne 0) { throw "Ingestion exited with code $LASTEXITCODE" } }
finally { Pop-Location }
