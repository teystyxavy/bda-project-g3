<#
.SYNOPSIS
Download selected IBM AML files locally, or run the HI-Small AWS pipeline.
.DESCRIPTION
PowerShell front door for the ingestion step of the IS459 Group 3 AML pipeline.
It does no data work itself: it validates switch combinations, picks a Python
interpreter, pins the AWS region, translates PowerShell-style switches into the
argparse sub-commands of scripts/ingestion/ingest.py, and runs that script from
the repository root so relative paths such as dataset/ and output/ resolve.

Modes (exactly one is chosen):
  -DownloadOnly  -> `ingest.py download`: fetch Kaggle IBM AML CSVs into
                    -DatasetDirectory (any subset). Nothing is sent to AWS.
  -PrepareOnly   -> `ingest.py prepare`: build transactions/accounts/fx CSVs
                    and manifest.json under output/ingest/<run-id>. No upload.
  (default)      -> `ingest.py run`: start the paid AWS workflow. By default
                    the dataset is acquired inside AWS (Lambda + cloud
                    Step Functions); -LocalAcquisition prepares and uploads
                    from this computer instead, and -Trigger EventBridge then
                    lets the S3 upload start the batch via EventBridge.
  -Status <id>   -> `ingest.py status`: print the Step Functions state.
  -Unlock <id>   -> `ingest.py unlock`: ask the control Lambda to release the
                    pipeline lock of a failed/aborted run.
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
# --- Parameters -------------------------------------------------------------
# Defaults of '' / 0 mean "not given"; the forwarding code below only passes
# options that were actually set, so ingest.py's own defaults apply otherwise.
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
# Any PowerShell error (including from Join-Path/Test-Path) stops the script.
$ErrorActionPreference = 'Stop'
# Guard 1: -Status/-Unlock only look up an existing run, so dataset selection
# or preview flags would be silently ignored; reject them instead.
if (($Status -or $Unlock) -and ($DryRun -or $ListOptions -or $Subset -or $Variant -or $Size -or $Files.Count -or $IncludePatterns)) {
    throw 'Selection, preview and listing options cannot be combined with -Status or -Unlock.'
}
# Guard 2: -DownloadOnly only fetches Kaggle files locally; pipeline,
# preparation and status switches make no sense with it.
if ($DownloadOnly -and ($PrepareOnly -or $Train -or $Rerun -or $NoWait -or $LocalAcquisition -or
        $FxFile -or $Deployment -or $RunId -or $Status -or $Unlock -or $PSBoundParameters.ContainsKey('Rows'))) {
    throw '-DownloadOnly cannot be combined with pipeline, preparation or status options. Use -DatasetDirectory, -DatasetVersion, -Refresh and file-selection options.'
}
# $PSScriptRoot is scripts/ingestion, so two Split-Path calls give the repo root.
$projectRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
# Prefer the project's Windows virtual environment (output/ml-venv) when it
# exists, because it has boto3/pandas/kagglehub installed; otherwise fall back
# to whatever 'python' is on PATH.
if (-not $Python) {
    $candidate = Join-Path $projectRoot 'output/ml-venv/Scripts/python.exe'
    $Python = if (Test-Path -LiteralPath $candidate) { $candidate } else { 'python' }
}
# Pin every AWS SDK call made by the child Python process to Singapore, where
# all project resources live, and tag requests with the same user-agent app id
# that ingest.py sets.
$env:AWS_REGION = 'ap-southeast-1'
$env:AWS_DEFAULT_REGION = 'ap-southeast-1'
$env:AWS_SDK_UA_APP_ID = 'AWSSkill-SageMaker'
# Build the argument list for ingest.py. Global options (--profile) must come
# before the sub-command name for argparse.
$arguments = @((Join-Path $PSScriptRoot 'ingest.py'), '--profile', $Profile)
# Choose the sub-command: status / unlock / download / prepare / run.
if ($Status) { $arguments += @('status', '--run-id', $Status) }
elseif ($Unlock) { $arguments += @('unlock', '--run-id', $Unlock) }
else {
    $arguments += $(if ($DownloadOnly) { 'download' } elseif ($PrepareOnly) { 'prepare' } else { 'run' })
    # Dataset selection options are forwarded only when non-empty.
    $arguments += @('--dataset-directory', $DatasetDirectory)
    foreach ($pair in @(@('--subset', $Subset), @('--variant', $Variant), @('--size', $Size))) {
        if ($pair[1]) { $arguments += @($pair[0], $pair[1]) }
    }
    # ingest.py's --files choices are lowercase (transactions/accounts/patterns).
    if ($Files.Count) { $arguments += '--files'; $arguments += @($Files | ForEach-Object { $_.ToLowerInvariant() }) }
    if ($IncludePatterns) { $arguments += '--include-patterns' }
    if ($DryRun) { $arguments += '--dry-run' }
    if ($ListOptions) { $arguments += '--list-options' }
    # Numeric/string options: forward when they have a value. -DatasetVersion
    # and -Rows are also forwarded when explicitly passed as 0, so ingest.py can
    # reject the invalid value instead of it being silently dropped.
    foreach ($pair in @(@('--dataset-version', $DatasetVersion), @('--rows', $Rows),
                         @('--fx-file', $FxFile), @('--deployment', $Deployment), @('--run-id', $RunId))) {
        if ($pair[1] -or ($pair[0] -eq '--dataset-version' -and $PSBoundParameters.ContainsKey('DatasetVersion')) -or
            ($pair[0] -eq '--rows' -and $PSBoundParameters.ContainsKey('Rows'))) {
            $arguments += @($pair[0], [string]$pair[1])
        }
    }
    # --train trains a private candidate without automatic promotion; --refresh
    # refreshes the Kaggle source; --rerun bypasses result and stage caches.
    if ($Train) { $arguments += '--train' }
    if ($Refresh) { $arguments += '--refresh' }
    if ($Rerun) { $arguments += '--rerun' }
    # Run-only switches are dropped for -PrepareOnly because the 'prepare'
    # sub-command does not define them (argparse would reject them).
    if ($NoWait -and -not $PrepareOnly) { $arguments += '--no-wait' }
    if ($LocalAcquisition -and -not $PrepareOnly) { $arguments += '--local-acquisition' }
    if ($Trigger -eq 'EventBridge' -and -not $PrepareOnly) { $arguments += @('--trigger', 'eventbridge') }
}
# Run from the repo root so relative paths (dataset/, output/ingest/) work, and
# convert a non-zero Python exit code into a PowerShell error. Pop-Location in
# 'finally' restores the caller's directory even when the run fails.
Push-Location $projectRoot
try { & $Python @arguments; if ($LASTEXITCODE -ne 0) { throw "Ingestion exited with code $LASTEXITCODE" } }
finally { Pop-Location }
