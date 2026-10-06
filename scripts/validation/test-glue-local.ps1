param(
    [string]$Image = 'public.ecr.aws/glue/aws-glue-libs@sha256:a54bd25fb72c55a2f28d07656a3cda943a042f345cc25f4c2c170667be864f01'
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
if (-not (Test-Path -LiteralPath (Join-Path $projectRoot 'output/aws-smoke/expected.parquet'))) {
    throw 'Prepare the sample first: python scripts/validation/prepare-aws-smoke.py'
}

docker run --rm `
    --mount "type=bind,source=$projectRoot,target=/home/hadoop/workspace,readonly" `
    -w /home/hadoop/workspace `
    --entrypoint bash $Image `
    -lc 'python3 -m pip install "pytest>=8,<10" && python3 -m pytest -q -p no:cacheprovider tests'
if ($LASTEXITCODE -ne 0) {
    throw "Glue container tests failed with exit code $LASTEXITCODE"
}
