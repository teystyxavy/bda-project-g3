# Offline regression tests in the pinned Glue 5 image. No AWS calls or secrets.
[CmdletBinding()]
param(
    [string]$WheelDirectory = 'output/cloud-scoring-20261005/linux-wheels',
    [string]$Image = 'public.ecr.aws/glue/aws-glue-libs@sha256:a54bd25fb72c55a2f28d07656a3cda943a042f345cc25f4c2c170667be864f01'
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$wheels = if ([IO.Path]::IsPathRooted($WheelDirectory)) { $WheelDirectory } else { Join-Path $projectRoot $WheelDirectory }
$wheels = (Resolve-Path -LiteralPath $wheels).Path
if (-not (Test-Path -LiteralPath (Join-Path $wheels 'runtime')) -or -not (Test-Path -LiteralPath (Join-Path $wheels 'test'))) {
    throw 'Need the existing runtime/ and test/ Linux wheel directories. See test-cloud-scoring-local.ps1 to prepare them.'
}
docker --host npipe:////./pipe/dockerDesktopLinuxEngine run --rm --network none `
    --mount "type=bind,source=$projectRoot,target=/home/hadoop/workspace,readonly" `
    --mount "type=bind,source=$wheels,target=/wheels,readonly" `
    -e AWS_EC2_METADATA_DISABLED=true -e OMP_NUM_THREADS=2 -e OPENBLAS_NUM_THREADS=2 `
    -w /home/hadoop/workspace --entrypoint bash $Image `
    -lc 'python3 -m pip install --no-index --find-links=/wheels/runtime --find-links=/wheels/test -r jobs/scoring-requirements.txt -r jobs/scoring-test-requirements.txt && python3 -m pytest -q -p no:cacheprovider tests --ignore=tests/spark --basetemp=/tmp/glue-stage-tests'
if ($LASTEXITCODE -ne 0) { throw 'Glue stage regression tests failed.' }
