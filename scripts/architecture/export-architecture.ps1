# export-architecture.ps1
# -----------------------
# Purpose: turn the generated draw.io diagrams into PNG images for the report
# and presentation slides, entirely on the local machine (no AWS calls, no
# downloads). It drives the draw.io Desktop command-line exporter.
#
# Inputs (under <repo>/planning/architecture/, produced by
# scripts/architecture/build-architecture-diagrams.py):
#   aml-pipeline-architecture.drawio  page 1 -> bq1.drawio.png
#                                     page 2 -> bq2.drawio.png
#   aml-pipeline-aws-icons.drawio     page 1 -> orchestration.drawio.png
#                                     page 2 -> streaming.drawio.png
# Output: the four PNGs in -OutputDirectory (default
# planning/architecture/exports, relative to the repository root).
#
# Usage (PowerShell, from any directory):
#   ./scripts/architecture/export-architecture.ps1
#   ./scripts/architecture/export-architecture.ps1 -DrawioExe 'C:\Program Files\draw.io\draw.io.exe'
#
# Parameters:
#   -DrawioExe        Path to a trusted draw.io Desktop executable. Defaults to
#                     a portable copy at <repo>/output/tools/drawio-31.7.0/.
#   -OutputDirectory  Where PNGs are written; relative paths resolve from the
#                     repository root, absolute paths are used as given.
#
# Failure behaviour: stops on the first error ($ErrorActionPreference = Stop)
# and throws if the exporter is missing, exits non-zero, or does not create
# the expected PNG. There are no retries.
#
# Note: the header is placed above param() on purpose; PowerShell allows
# comments before the param block but no other statements.
param(
    [string]$DrawioExe,
    [string]$OutputDirectory = 'planning/architecture/exports'
)
# Treat every error as terminating so a failed step cannot be silently skipped.
$ErrorActionPreference = 'Stop'
# $PSScriptRoot is scripts/architecture; two Split-Path -Parent calls climb to
# the repository root, so the script works regardless of the current directory.
$taskRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
# Fall back to the portable draw.io build kept under output/tools (not in git).
if (-not $DrawioExe) {
    $DrawioExe = Join-Path $taskRoot 'output/tools/drawio-31.7.0/draw.io.exe'
}
# Refuse to continue without a real exporter. The script never fetches software
# itself, so the user must point it at a copy they trust.
if (-not (Test-Path -LiteralPath $DrawioExe -PathType Leaf)) {
    throw 'Provide -DrawioExe pointing to a trusted draw.io Desktop exporter (tested with 31.7.0). This script does not download or install software.'
}
# Resolve the output folder against the repo root unless an absolute path was given,
# then create it (-Force = no error if it already exists).
$taskTarget = if ([IO.Path]::IsPathRooted($OutputDirectory)) { $OutputDirectory } else { Join-Path $taskRoot $OutputDirectory }
New-Item -ItemType Directory -Force -Path $taskTarget | Out-Null
# Export plan: (diagram file name without extension, 1-based page number, PNG name).
$taskEntries = @(
    @('aml-pipeline-architecture', 1, 'bq1'),
    @('aml-pipeline-architecture', 2, 'bq2'),
    @('aml-pipeline-aws-icons', 1, 'orchestration'),
    @('aml-pipeline-aws-icons', 2, 'streaming')
)
# Export one page per iteration.
foreach ($taskEntry in $taskEntries) {
    $taskInput = Join-Path $taskRoot ('planning/architecture/' + $taskEntry[0] + '.drawio')
    $taskOutput = Join-Path $taskTarget ($taskEntry[2] + '.drawio.png')
    # draw.io CLI flags: -x export mode, -f png format, -p page number,
    # -e embed the diagram XML inside the PNG (so the image stays editable in
    # draw.io), -b 10 px border, -o output file. Paths are wrapped in quotes
    # because Start-Process joins the argument list into one command line and
    # paths may contain spaces.
    $taskArgs = @('-x', '-f', 'png', '-p', $taskEntry[1], '-e', '-b', '10', '-o', ('"' + $taskOutput + '"'), ('"' + $taskInput + '"'))
    # Run hidden and wait for completion; -PassThru returns the process so the
    # exit code can be checked.
    $taskProcess = Start-Process -FilePath $DrawioExe -ArgumentList $taskArgs -WindowStyle Hidden -PassThru -Wait
    # Treat a non-zero exit code or a missing PNG as failure.
    if ($taskProcess.ExitCode -ne 0 -or -not (Test-Path -LiteralPath $taskOutput)) {
        throw ('Local export failed: ' + $taskEntry[2])
    }
    Write-Output ('Exported locally: ' + $taskOutput)
}
