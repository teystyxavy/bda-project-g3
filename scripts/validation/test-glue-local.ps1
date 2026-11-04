# test-glue-local.ps1
# -------------------
# Purpose: run the project's pytest suite inside the official AWS Glue Docker
# image (aws-glue-libs, pinned by digest) so the PySpark curation job is tested
# with the same Spark/Python versions that AWS Glue uses - without running
# anything on AWS.
#
# Prerequisite: the smoke fixture output/aws-smoke/expected.parquet, created
# by `python scripts/validation/prepare-aws-smoke.py`.
#
# What happens: the repository is bind-mounted read-only at
# /home/hadoop/workspace, pytest is installed into the throwaway container
# (--rm deletes it afterwards) and `pytest tests` runs. The script throws with
# the container's exit code if any test fails.
#
# Usage: ./scripts/validation/test-glue-local.ps1 [-Image <image ref>]
# (requires Docker). Comments are placed above param() because PowerShell
# requires param() to be the first statement.
param(
    [string]$Image = 'public.ecr.aws/glue/aws-glue-libs@sha256:a54bd25fb72c55a2f28d07656a3cda943a042f345cc25f4c2c170667be864f01'
)

# Stop on the first error.
$ErrorActionPreference = 'Stop'
# Repository root: two levels above scripts/validation.
$projectRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
# Fail early with a helpful message if the smoke fixture is missing.
if (-not (Test-Path -LiteralPath (Join-Path $projectRoot 'output/aws-smoke/expected.parquet'))) {
    throw 'Prepare the sample first: python scripts/validation/prepare-aws-smoke.py'
}

# Backticks (`) continue the docker command over several lines. -p no:cacheprovider
# stops pytest writing a .pytest_cache folder into the read-only mount.
# The pytest pin is in bash single quotes (written '' inside this PowerShell
# string): Windows PowerShell 5.1 strips embedded double quotes from native
# arguments, which made bash read ">=8,<10" as a file redirect.
docker run --rm `
    --mount "type=bind,source=$projectRoot,target=/home/hadoop/workspace,readonly" `
    -w /home/hadoop/workspace `
    --entrypoint bash $Image `
    -lc 'python3 -m pip install ''pytest>=8,<10'' && python3 -m pytest -q -p no:cacheprovider tests'
# $LASTEXITCODE holds the exit code of the last native command (docker).
if ($LASTEXITCODE -ne 0) {
    throw "Glue container tests failed with exit code $LASTEXITCODE"
}
