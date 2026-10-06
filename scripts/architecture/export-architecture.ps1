param(
    [string]$DrawioExe,
    [string]$OutputDirectory = 'planning/architecture/exports'
)
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
if (-not $DrawioExe) {
    $DrawioExe = Join-Path $taskRoot 'output/tools/drawio-31.7.0/draw.io.exe'
}
if (-not (Test-Path -LiteralPath $DrawioExe -PathType Leaf)) {
    throw 'Provide -DrawioExe pointing to a trusted draw.io Desktop exporter (tested with 31.7.0). This script does not download or install software.'
}
$taskTarget = if ([IO.Path]::IsPathRooted($OutputDirectory)) { $OutputDirectory } else { Join-Path $taskRoot $OutputDirectory }
New-Item -ItemType Directory -Force -Path $taskTarget | Out-Null
$taskEntries = @(
    @('aml-pipeline-architecture', 1, 'bq1'),
    @('aml-pipeline-architecture', 2, 'bq2'),
    @('aml-pipeline-aws-icons', 1, 'orchestration'),
    @('aml-pipeline-aws-icons', 2, 'streaming')
)
foreach ($taskEntry in $taskEntries) {
    $taskInput = Join-Path $taskRoot ('planning/architecture/' + $taskEntry[0] + '.drawio')
    $taskOutput = Join-Path $taskTarget ($taskEntry[2] + '.drawio.png')
    $taskArgs = @('-x', '-f', 'png', '-p', $taskEntry[1], '-e', '-b', '10', '-o', ('"' + $taskOutput + '"'), ('"' + $taskInput + '"'))
    $taskProcess = Start-Process -FilePath $DrawioExe -ArgumentList $taskArgs -WindowStyle Hidden -PassThru -Wait
    if ($taskProcess.ExitCode -ne 0 -or -not (Test-Path -LiteralPath $taskOutput)) {
        throw ('Local export failed: ' + $taskEntry[2])
    }
    Write-Output ('Exported locally: ' + $taskOutput)
}
