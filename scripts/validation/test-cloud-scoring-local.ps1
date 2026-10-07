# test-cloud-scoring-local.ps1
# ----------------------------
# Purpose: run the frozen-model Glue scoring tests locally inside the official
# AWS Glue Docker image (aws-glue-libs, pinned by digest), so we can prove the
# Glue scoring job (jobs/score_transactions.py) reproduces the native XGBoost
# scores before running it on AWS. No AWS credentials are mounted and no AWS
# jobs are started.
#
# Inputs:
#   output/<PackageName>/package.json  - the frozen cloud-scoring package
#       (model artifacts + fixture) prepared earlier per the runbook. The
#       tests find it through the CLOUD_SCORING_PACKAGE environment variable.
#   jobs/scoring-requirements.txt, jobs/scoring-test-requirements.txt
# Output: Linux wheels cached in output/cloud-scoring-20261005/linux-wheels;
#   test results printed by pytest. The script throws if any step fails.
#
# Two docker runs:
#   1. Online: download Linux binary wheels (from PyPI) inside the Glue image,
#      so the packages match the container's Python/OS rather than Windows.
#   2. Offline: install only from those wheels (--no-index) and run pytest.
#      The repository is mounted read-only, so tests cannot modify sources.
#
# Usage: ./scripts/validation/test-cloud-scoring-local.ps1 [-PackageName <name>] [-Image <ref>]
# (requires Docker Desktop). Comments sit above param() because PowerShell
# requires param() to be the first statement.
param(
    [string]$Image = 'public.ecr.aws/glue/aws-glue-libs@sha256:a54bd25fb72c55a2f28d07656a3cda943a042f345cc25f4c2c170667be864f01',
    # Only letters, digits, '_' and '-' are allowed so the name is safe inside paths.
    [ValidatePattern('^[a-zA-Z0-9_-]+$')]
    [string]$PackageName = 'cloud-scoring-verified-20261005'
)

# Stop on the first error.
$ErrorActionPreference = 'Stop'
# Repository root: two levels above scripts/validation.
$projectRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$packageRoot = Join-Path $projectRoot "output/$PackageName"
if (-not (Test-Path -LiteralPath (Join-Path $packageRoot 'package.json'))) {
    throw 'Prepare the frozen cloud-scoring package first; see the runbook.'
}
# Wheel cache on the host, shared by both container runs.
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
