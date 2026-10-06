[CmdletBinding()]
param(
    [string]$Python = '', [string]$Profile = 'is459-g3-tools',
    [string]$DatasetDirectory = 'dataset', [int]$DatasetVersion = 0, [int]$Rows = 0,
    [string]$FxFile = '', [string]$Deployment = '', [string]$RunId = '',
    [switch]$PrepareOnly, [switch]$Train, [switch]$Refresh, [switch]$Rerun,
    [switch]$NoWait, [switch]$LocalAcquisition, [string]$Status = '', [string]$Unlock = ''
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
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
    $arguments += $(if ($PrepareOnly) { 'prepare' } else { 'run' })
    $arguments += @('--dataset-directory', $DatasetDirectory)
    foreach ($pair in @(@('--dataset-version', $DatasetVersion), @('--rows', $Rows),
                         @('--fx-file', $FxFile), @('--deployment', $Deployment), @('--run-id', $RunId))) {
        if ($pair[1]) { $arguments += @($pair[0], [string]$pair[1]) }
    }
    if ($Train) { $arguments += '--train' }
    if ($Refresh) { $arguments += '--refresh' }
    if ($Rerun) { $arguments += '--rerun' }
    if ($NoWait -and -not $PrepareOnly) { $arguments += '--no-wait' }
    if ($LocalAcquisition -and -not $PrepareOnly) { $arguments += '--local-acquisition' }
}
Push-Location $projectRoot
try { & $Python @arguments; if ($LASTEXITCODE -ne 0) { throw "Ingestion exited with code $LASTEXITCODE" } }
finally { Pop-Location }
