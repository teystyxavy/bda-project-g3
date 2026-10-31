# Retrieve the deployed inputs/artifacts for a reviewable plan on another machine.
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$Stack,
    [string]$Profile = 'is459-g3-tools',
    [string]$Terraform = 'terraform'
)
$ErrorActionPreference = 'Stop'
& (Join-Path $PSScriptRoot 'init-terraform.ps1') -Stack $Stack -Profile $Profile -Terraform $Terraform
$projectRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$module = if ($Stack -eq 'foundation') { 'terraform' } else { $Stack }
$localRoot = Join-Path $env:LOCALAPPDATA "IS459-G3/shared-inputs/$Stack"
[IO.Directory]::CreateDirectory($localRoot) | Out-Null
$metadataFile = Join-Path $localRoot 'artifacts.json'
& aws s3api get-object --bucket is459-g3-tfstate-588738581042 --key handoff/current/artifacts.json --profile $Profile --region ap-southeast-1 $metadataFile --no-cli-pager | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Cannot read the shared input inventory.' }
$inventory = Get-Content -LiteralPath $metadataFile -Raw | ConvertFrom-Json
$metadata = $inventory.stacks.$Stack
if (-not $metadata) { throw 'No prepared input inventory for this stack.' }
$stateText = & $Terraform "-chdir=$projectRoot/infra/$module" state pull
if ($LASTEXITCODE -ne 0) { throw 'Cannot read shared state.' }
$state = $stateText | ConvertFrom-Json
if ($state.lineage -ne $metadata.lineage -or $state.serial -ne $metadata.serial) {
    throw 'Shared state has changed since these inputs were prepared. Refresh the handoff inputs before planning.'
}
$variablesFile = Join-Path $localRoot 'terraform.tfvars.json'
& aws s3api get-object --bucket is459-g3-tfstate-588738581042 --key "handoff/current/inputs/$Stack.tfvars.json" --profile $Profile --region ap-southeast-1 $variablesFile --no-cli-pager | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Cannot retrieve deployed Terraform inputs.' }
$variables = Get-Content -LiteralPath $variablesFile -Raw | ConvertFrom-Json
if ($metadata.objects.Count) {
    $artifactRoot = Join-Path $localRoot 'artifacts'
    [IO.Directory]::CreateDirectory($artifactRoot) | Out-Null
    foreach ($item in $metadata.objects) {
        $target = [IO.Path]::GetFullPath((Join-Path $artifactRoot $item.file))
        if (-not $target.StartsWith($artifactRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
            throw 'Artifact path is outside the private download directory.'
        }
        [IO.Directory]::CreateDirectory((Split-Path -Parent $target)) | Out-Null
        & aws s3api get-object --bucket $item.bucket --key $item.key --profile $Profile --region ap-southeast-1 $target --no-cli-pager | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "Cannot retrieve artifact $($item.file)." }
        if ((Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash.ToLowerInvariant() -ne $item.sha256) {
            throw "Artifact checksum mismatch: $($item.file)."
        }
    }
    $variables.artifact_directory = $artifactRoot.Replace('\', '/')
}
if ($metadata.lambda_function) {
    $lambdaText = & aws lambda get-function --function-name $metadata.lambda_function --profile $Profile --region ap-southeast-1 --output json --no-cli-pager
    if ($LASTEXITCODE -ne 0) { throw 'Cannot retrieve the deployed Lambda package.' }
    $lambda = $lambdaText | ConvertFrom-Json
    $zip = Join-Path $localRoot 'lambda.zip'
    Invoke-WebRequest -Uri $lambda.Code.Location -OutFile $zip
    $hasher = [Security.Cryptography.SHA256]::Create()
    $stream = [IO.File]::OpenRead($zip)
    try { $actualHash = [Convert]::ToBase64String($hasher.ComputeHash($stream)) }
    finally { $stream.Dispose(); $hasher.Dispose() }
    if ($actualHash -ne $lambda.Configuration.CodeSha256 -or $actualHash -ne $metadata.lambda_code_sha256) {
        throw 'Deployed Lambda package differs from the shared input inventory.'
    }
    $variables.lambda_zip_path = $zip.Replace('\', '/')
}
[IO.File]::WriteAllText($variablesFile, ($variables | ConvertTo-Json -Depth 30), [Text.UTF8Encoding]::new($false))
Write-Host "Private inputs saved to $variablesFile"
Write-Host "Review with: terraform -chdir=infra/$module plan -var-file=`"$variablesFile`""
