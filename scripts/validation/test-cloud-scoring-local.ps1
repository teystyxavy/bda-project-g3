param(
    [string]$Image = 'public.ecr.aws/glue/aws-glue-libs@sha256:a54bd25fb72c55a2f28d07656a3cda943a042f345cc25f4c2c170667be864f01',
    [ValidatePattern('^[a-zA-Z0-9_-]+$')]
    [string]$PackageName = 'cloud-scoring-verified-20261005'
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$packageRoot = Join-Path $projectRoot "output/$PackageName"
if (-not (Test-Path -LiteralPath (Join-Path $packageRoot 'package.json'))) {
    throw 'Prepare the frozen cloud-scoring package first; see the runbook.'
}
$wheelRoot = Join-Path $projectRoot 'output/cloud-scoring-20261005/linux-wheels'
New-Item -ItemType Directory -Force -Path $wheelRoot | Out-Null

# Download native Linux wheels in the matching image, not on Windows. These
# downloads are local/PyPI only; no AWS credentials are mounted or AWS jobs run.
docker run --rm `
    --mount "type=bind,source=$projectRoot,target=/home/hadoop/workspace,readonly" `
    --mount "type=bind,source=$wheelRoot,target=/wheels" `
    -w /home/hadoop/workspace --entrypoint bash $Image `
    -lc 'python3 -m pip download --only-binary=:all: -r jobs/scoring-requirements.txt --dest /wheels/runtime && python3 -m pip download --only-binary=:all: -r jobs/scoring-test-requirements.txt --dest /wheels/test'
if ($LASTEXITCODE -ne 0) { throw 'Linux wheel download failed.' }

# Installation is offline from these wheels; container writes are ephemeral.
# The project/model/input bind mount remains read-only throughout the test.
docker run --rm `
    --mount "type=bind,source=$projectRoot,target=/home/hadoop/workspace,readonly" `
    --mount "type=bind,source=$wheelRoot,target=/wheels,readonly" `
    -e "CLOUD_SCORING_PACKAGE=/home/hadoop/workspace/output/$PackageName" `
    -w /home/hadoop/workspace --entrypoint bash $Image `
    -lc 'python3 -m pip install --no-index --find-links=/wheels/runtime --find-links=/wheels/test -r jobs/scoring-requirements.txt -r jobs/scoring-test-requirements.txt && python3 -m pytest -q -p no:cacheprovider tests'
if ($LASTEXITCODE -ne 0) { throw 'Glue frozen-scoring parity test failed.' }
