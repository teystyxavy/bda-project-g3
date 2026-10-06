# Streaming failure isolation — revised deployment review

**The extension is approved and applied: fourteen resources are deployed.
The single replay reconciled all 884 results and 32 sender histories;
the consumer is verified Disabled and Terraform has no drift.** The exact reviewed image was uploaded and its digest read
back. Lambda is Active; an empty-event invocation loaded the hash-checked frozen
model and returned HTTP 200 with `batchItemFailures: []`. The mapping was created Disabled, passed empty-namespace/security preflight,
and was then activated for the one approved replay. The producer finished successfully in 1,049.545
seconds; its 884 unique receipt identities and increasing sequence numbers
passed local verification. No transaction was replayed again. See [final deployment evidence](../../evidence/streaming-deployment-result-20261006.json)
and [the completed result](streaming-demo-result-20261006.md).

## Why the original deployment could not finish

ECR rejected the explicit Resource fields accepted by generic Access Analyzer.
The deployed repository-attached statement omits Resource and uses StringLike
with the **literal exact** source function ARN, including account and region;
there is no ARN wildcard. This follows the Lambda repository-policy format.
This does not grant access to other repositories. Four failed policy attempts
and the successful correction are retained outside OneDrive.

Lambda then rejected creation of the disabled mapping: **“The function
execution role does not have permissions to call PutObject on S3.”** The role
already allowed `aws/lambda/*` in the existing project bucket, matching the
documented archive layout. Adding the exact bucket ARN for validation also
failed. Terraform retried the permission check for ten minutes; no mapping was
created. The subsequent single immediate creation attempt also failed.

The [AWS destination documentation](https://docs.aws.amazon.com/lambda/latest/dg/kinesis-on-failure-destination.html)
illustrates bucket/object write permissions, whereas the prefix-restricted
permissions did not satisfy the actual validation. We therefore propose a
dedicated bucket to support that validation **without giving the role broad
write access to existing raw, curated, model or dashboard objects**. This is a
service compatibility finding; the new arrangement was subsequently deployed and passed Lambda's check.

## Concrete approval extension

New bucket: `is459-g3-stream-failures-588738581042-ap-southeast-1`.
Three additional Terraform resources create the bucket, block all public access
and explicitly configure AES256 encryption. `force_destroy=false`; no expiry,
automatic cleanup, object deletion or new KMS key is configured. Bucket-owner
conditions remain in the execution policy. Failure-object PutObject and
destination ListBucket move to this isolated bucket; application audit writes
remain restricted to the existing run's audit prefix with SourceFunctionArn.
No object Get/Delete permission is added. This removes the need to list the
existing project bucket through the failure-destination permission.

The original approval already covers the mapping, activation, one 884-event
replay, read-only reconciliation and disabling consumption. The extension is
**only the three S3 resources and the execution-policy update**. The final
stack would contain fourteen Terraform resources. The authenticated saved plan
shows **four creates** (three S3 resources plus the previously approved mapping),
**one execution-policy update**, and **zero deletes**. No other existing
resource changes. Its mapping remains disabled.

Private plan: `%LOCALAPPDATA%/IS459-G3/streaming-isolated-failures-review-20261006.tfplan`.
Hashes and resource addresses are in the linked evidence. Terraform validates;
six mocked tests pass, including failure-bucket privacy, retained objects and
no mapping when the additional bucket is not enabled. The revised identity
policy has zero Access Analyzer findings. The refreshed matching plan was applied: four resources added, one policy
updated, no resources deleted. A separate one-update plan enabled the mapping.

The bucket has no hourly infrastructure charge; storage and requests are
usage-based. The original USD 0.10–0.50 one-hour demonstration estimate remains
the planning allowance, not a hard cap. Failure storage would be tiny for this
bounded cohort; actual charges depend on requests/records. The deployed shard
already incurs **USD 0.0184/hour**, about USD 13.69 over 744 hours, until explicit
cleanup. The existing USD 10 alert is not a spend cap. No transaction replay,
replacement paid run or cleanup is authorized by this review alone.

## Image scan and bounded use assessment

ECR's basic OS-package scan completed with **8 HIGH, 6 MEDIUM and 1 LOW findings**.
It is not a clean scan and does not constitute a complete Python dependency
audit. The high findings concern PCRE2 JIT matching, RPM source/gem processing
and curl pinning under disabled certificate verification. The medium/low
findings concern libxml2. This fixed synthetic replay does not invoke RPM,
PCRE2 JIT, XML parsing or curl with disabled verification; boto3 uses normal
verified TLS and no user-controlled code/model is loaded. That bounds the
identified attack paths for this approved demonstration, rather than fixing
the vulnerable packages. Full scan details are retained in
`%LOCALAPPDATA%/IS459-G3/streaming-ecr-scan-20261006.json`. Patch/rebuild and repeat
parity/scan review before broader reuse; do not describe the image as secure
solely because model parity passes.

The approved extension, bucket security checks, empty-namespace preflight,
activation, single replay, full reconciliation and consumer disable are complete.
All 884 audit/results and 32 sender versions passed. Logs and metrics show zero
errors, throttles and record failures; the isolated failure archive is empty.
Resources and evidence are retained. Another replay or cleanup requires explicit
authorization.
