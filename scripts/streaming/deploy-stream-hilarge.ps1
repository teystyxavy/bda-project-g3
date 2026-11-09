# deploy-stream-hilarge.ps1 - switch the dashboard's Incoming AML tab to an HI-Large replay.
#
# Run stream-hilarge-20261109: 32 complete HI-Large sender histories (Athena cohort,
# scripts/streaming/hi-large-cohort.sql) scored by the same frozen delta1 model as the
# 7 Nov stream. Everything uses its own Terraform state and resource names, so the
# stream-delta1-20261107 stack is never planned against.
#
# What it does (about 20-30 minutes, under USD 1):
#   1. Terraform (state streaming-hilarge-20261109): ECR repo, DynamoDB table, roles, logs, alarm.
#   2. Copies the reviewed delta1 consumer image into the new repo (same digest, no Docker).
#   3. Uploads the reviewed events.jsonl, adds the consumer Lambda, a 1-shard Kinesis stream,
#      a disabled mapping and the dashboard publisher.
#   4. Preloads the receivers, enables the consumer, replays the events at 10/s.
#   5. Publishes the snapshot until every event is scored and reconciled.
#   6. Parks the stream (deletes the shard and mapping; keeps table, function, publisher).
#   7. Points dashboard/streaming-config.json at the new run and invalidates it.
# The batch pipeline, current.json, demo.json and the main dashboard files are never touched.
#
# Default = checks + plan only. -Execute does the steps above.
#   powershell -ExecutionPolicy Bypass -File scripts\streaming\deploy-stream-hilarge.ps1 [-Execute]
# Rollback of the tab: re-upload output\stream-hilarge-20261109\run-*\streaming-config.before.json
# as dashboard/streaming-config.json (the 7 Nov HI-Medium snapshot is still in S3).
param([switch]$Execute, [string]$Package = '', [string]$AwsProfile = 'is459-g3-tools')
$ErrorActionPreference = 'Stop'
$Repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$RunId = 'stream-hilarge-20261109'
$Name = "is459-g3-$RunId"
$RepoName = 'is459-g3-streaming-hilarge'
$SourceRepo = 'is459-g3-streaming-delta1'
$ImageDigest = 'sha256:63d29f35ca8ec7ba4b7730b05d1c01ac530bb59e1558bb06a10024208944ee86'   # live delta1 consumer image
$Manifest = '0b9ba30d77237c9f71a1799235316d9f6dfba042dc70c1c31546513da0ea4b6a'             # frozen delta1 model in that image
$Bucket = 'is459-g3-588738581042-ap-southeast-1'
$Site = 'is459-g3-dashboard-588738581042-ap-southeast-1'
$Distribution = 'EU1WB6XM4GKHQ'
if (-not $Package) { $Package = Join-Path $Repo 'output\stream-hilarge-20261109\streaming-demo-hilarge-20261109' }
$Out = Join-Path $Repo 'output\stream-hilarge-20261109'
$Run = Join-Path $Out ('run-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
New-Item -ItemType Directory -Force $Run | Out-Null
$env:AWS_PROFILE = $AwsProfile; $env:AWS_REGION = 'ap-southeast-1'; $env:AWS_DEFAULT_REGION = 'ap-southeast-1'
$env:PYTHONPATH = Join-Path $Repo 'src'
$Python = Join-Path $Repo 'output\ml-venv\Scripts\python.exe'
if (-not (Test-Path $Python)) { $Python = (Get-Command python -CommandType Application | Select-Object -First 1).Source }
$AwsExe = (Get-Command aws -CommandType Application | Select-Object -First 1).Source
$Terraform = Join-Path $Repo 'output\tools\terraform\terraform.exe'
if (-not (Test-Path $Terraform)) { $Terraform = (Get-Command terraform -CommandType Application | Select-Object -First 1).Source }
function Fail($m) { Write-Host "STOP: $m" -ForegroundColor Red; exit 1 }
function Ok($m)   { Write-Host "OK   $m" -ForegroundColor Green }
function Step($m) { Write-Host "---- $m" -ForegroundColor Cyan }
function AwsCli { $out = & $AwsExe @args; if ($LASTEXITCODE -ne 0) { Fail "aws $($args -join ' ')" }; $out }
function Py { & $Python @args; if ($LASTEXITCODE -ne 0) { Fail "python $($args -join ' ')" } }

# ---- 0. preflight (read-only) ----
Step 'preflight'
if ((AwsCli sts get-caller-identity --query Account --output text) -ne '588738581042') { Fail 'wrong AWS account' }
$check = Join-Path $Package 'verification.json'
if (-not (Test-Path $check)) { Fail "replay package not found: $Package" }
$v = Get-Content $check -Raw | ConvertFrom-Json
if ($v.status -ne 'local-parity-passed-not-deployed' -or $v.run_id -ne $RunId -or -not $v.all_features_exact) { Fail 'replay package is not the verified HI-Large package' }
foreach ($f in @(@('events.jsonl', $v.events_sha256), @('receivers.jsonl', $v.receivers_sha256), @('replay.csv', $v.replay_sha256))) {
  if ((Get-FileHash (Join-Path $Package $f[0]) -Algorithm SHA256).Hash.ToLower() -ne $f[1]) { Fail "$($f[0]) changed since verification" }
}
$EventsSha = $v.events_sha256
Ok "package: $($v.rows) HI-Large events, $($v.senders) senders, $($v.receivers) receivers, max score diff $($v.max_absolute_score_difference)"
$img = AwsCli ecr describe-images --repository-name $SourceRepo --image-ids "imageDigest=$ImageDigest" --query 'imageDetails[0].imageDigest' --output text
if ($img -ne $ImageDigest) { Fail 'reviewed delta1 consumer image not found' }
$streams = AwsCli kinesis list-streams --query 'length(StreamNames)' --output text
if ($streams.Trim() -ne '0') { Write-Host "WARN another Kinesis stream exists ($streams); it is not touched." -ForegroundColor Yellow }
AwsCli s3 cp "s3://$Site/dashboard/streaming-config.json" (Join-Path $Run 'streaming-config.before.json') --only-show-errors | Out-Null
Ok 'saved the current streaming-config.json (rollback copy)'

# Publisher zip, built from this checkout's src/bda_pipeline/dashboard_streaming.py.
$ZipDir = Join-Path $Run 'publisher'
Py (Join-Path $Repo 'scripts\aws\prepare-dashboard-streaming.py') --output $ZipDir | Out-Null
$Zip = (Join-Path $ZipDir 'dashboard-streaming.zip') -replace '\\', '/'

# ---- Terraform ----
$Stack = Join-Path $Repo 'infra\streaming'
$env:TF_DATA_DIR = Join-Path $env:LOCALAPPDATA "IS459-G3\terraform-$RunId"
New-Item -ItemType Directory -Force $env:TF_DATA_DIR | Out-Null
& $Terraform "-chdir=$Stack" init -input=false -reconfigure -no-color '-backend-config=../shared.s3.tfbackend' "-backend-config=key=streaming-hilarge-20261109/terraform.tfstate" | Out-Null
if ($LASTEXITCODE -ne 0) { Fail 'terraform init' }
$Base = @('-var', 'provision_streaming=true', '-var', "run_id=$RunId", '-var', "repository_name=$RepoName",
          '-var', 'use_existing_failure_bucket=true', '-var', "model_manifest_sha256=$Manifest")
$Image = @('-var', "image_uri=588738581042.dkr.ecr.ap-southeast-1.amazonaws.com/$RepoName@$ImageDigest")
$Pub = @('-var', 'provision_publisher=true', '-var', "lambda_zip_path=$Zip", '-var', "replay_events_sha256=$EventsSha")
function Apply($label, [string[]]$vars, [switch]$PlanOnly) {
  $plan = Join-Path $env:TF_DATA_DIR "$label.tfplan"
  & $Terraform "-chdir=$Stack" plan -input=false -no-color -out=$plan @vars | Tee-Object (Join-Path $Run "$label.plan.txt") | Select-String -Pattern 'Plan:|No changes|will be (created|destroyed|updated)|must be replaced'
  if ($LASTEXITCODE -ne 0) { Fail "terraform plan ($label)" }
  if ($PlanOnly) { return }
  & $Terraform "-chdir=$Stack" apply -input=false -no-color $plan | Tee-Object (Join-Path $Run "$label.apply.txt") | Select-String -Pattern 'Apply complete'
  if ($LASTEXITCODE -ne 0) { Fail "terraform apply ($label)" }
}

if (-not $Execute) {
  Step 'plan only (nothing changed). Base stack:'
  Apply 'base' $Base -PlanOnly
  Py (Join-Path $Repo 'scripts\streaming\replay-streaming-demo.py') --package $Package
  Write-Host "`nChecks passed. Re-run with -Execute to deploy, replay and publish (about 20-30 min)." -ForegroundColor Green
  exit 0
}

# ---- 1. base stack ----
Step '1/7 base stack (repo, table, roles, logs, alarm)'
Apply 'base' $Base
# ---- 2. image ----
Step '2/7 copy the delta1 consumer image (about 340 MB, same digest)'
Py (Join-Path $Repo 'scripts\streaming\copy-streaming-image.py') --source $SourceRepo --target $RepoName --digest $ImageDigest --profile $AwsProfile
# ---- 3. events, function, stream, publisher ----
Step '3/7 upload events, add consumer, stream and publisher'
AwsCli s3 cp (Join-Path $Package 'events.jsonl') "s3://$Bucket/streaming/$RunId/input/events.jsonl" --sse AES256 --content-type application/x-ndjson --only-show-errors | Out-Null
Apply 'stream' ($Base + $Image + @('-var', 'provision_stream=true') + $Pub)
# ---- 4. receivers, consumer, replay ----
Step '4/7 preload receivers, enable consumer, replay'
Py (Join-Path $Repo 'scripts\streaming\seed-streaming-receivers.py') --package $Package --execute-approved --profile $AwsProfile
Apply 'consumer' ($Base + $Image + @('-var', 'provision_stream=true', '-var', 'enable_consumer=true', '-var', 'replay_approved=true') + $Pub)
Py (Join-Path $Repo 'scripts\streaming\replay-streaming-demo.py') --package $Package --execute-approved --profile $AwsProfile --rate 10 --receipt (Join-Path $Run 'receipt.jsonl')
# ---- 5. publish until complete ----
Step '5/7 publish snapshots until every event is scored'
$done = $false
for ($i = 1; $i -le 40 -and -not $done; $i++) {
  Start-Sleep -Seconds 15
  $resp = Join-Path $Run 'publisher-response.json'
  AwsCli lambda invoke --function-name "$Name-publisher" --cli-binary-format raw-in-base64-out --payload '{"once":true}' $resp | Out-Null
  $r = Get-Content $resp -Raw | ConvertFrom-Json
  Write-Host "     $($r.state): $($r.processed)/$($r.expected)"
  if ($r.errorMessage) { Write-Host "     publisher error: $($r.errorMessage)" -ForegroundColor Yellow }
  if ($r.processed -eq $r.expected -and $r.expected -gt 0 -and $r.state -in @('running', 'completed', 'paused')) { $done = $true }
}
if (-not $done) { Write-Host 'WARN not every event was scored in 10 minutes; parking anyway (the table keeps what was scored).' -ForegroundColor Yellow }
# ---- 6. park ----
Step '6/7 park the stream (delete shard and mapping, consumer off)'
Apply 'parked' ($Base + $Image + $Pub)
AwsCli lambda invoke --function-name "$Name-publisher" --cli-binary-format raw-in-base64-out --payload '{"once":true}' (Join-Path $Run 'publisher-final.json') | Out-Null
$final = Get-Content (Join-Path $Run 'publisher-final.json') -Raw | ConvertFrom-Json
Ok "final snapshot: $($final.state), $($final.processed)/$($final.expected)"
if ($final.state -ne 'completed') { Fail "the run is $($final.state), not completed; streaming-config.json NOT switched. Send me $Run." }
# ---- 7. point the Incoming tab at the new run ----
Step '7/7 switch the Incoming tab'
$cfg = Join-Path $Run 'streaming-config.json'
[IO.File]::WriteAllText($cfg, "{`"schema_version`":1,`"run_id`":`"$RunId`",`"snapshot_url`":`"/streaming/$RunId/snapshot.json`"}`n")
AwsCli s3 cp $cfg "s3://$Site/dashboard/streaming-config.json" --content-type application/json --cache-control 'no-store,max-age=0' --sse AES256 --only-show-errors | Out-Null
AwsCli cloudfront create-invalidation --distribution-id $Distribution --paths '/streaming-config.json' | Out-Null
AwsCli kinesis list-streams --query StreamNames --output text | Out-File (Join-Path $Run 'streams-after.txt')
Ok "Incoming tab now reads $RunId. Evidence and plans: $Run"
Write-Host 'Open https://d1n61tlw9pcrog.cloudfront.net/ -> Incoming AML (hard refresh).' -ForegroundColor Green
