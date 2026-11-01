# Coursework archive bucket

Private consolidation bucket:
`is459-g3-archive-588738581042-ap-southeast-1` (Singapore).

This stack manages the archive bucket and its safeguards: public access is
blocked, object ACLs are disabled, AES256 encryption and versioning are enabled,
and the bucket policy denies non-HTTPS access. `force_destroy=false` protects
nonempty archives. The stack does not start pipeline jobs or expire archive data.

Objects use `buckets/<original-bucket>/<original-key>`. Migration inventories,
original bucket configuration, the retired lab job definition and per-object
SHA-256 receipts are under `_archive/20261101/`. These records are private.
Object contents, user metadata and tags are copied; public access permissions
are not carried over. Original bucket names cannot be restored as nested buckets.

The archived data uses S3 Standard so it remains immediately readable. This is
organizational consolidation, with normal storage and request charges and
one-time cross-region copying charges for the US East sources. No Glacier
transition or automatic expiration was introduced. See
[AWS pricing](https://aws.amazon.com/s3/pricing/).

The authoritative state is `archive/terraform.tfstate` in the existing shared
state bucket. Connect from the repository root:

```powershell
.\scripts\aws\init-terraform.ps1 -Stack archive
terraform -chdir=infra/archive plan -input=false
```

There are no variable inputs. Review any future changes before applying them.
See the [archive record](../../planning/runbooks/s3-archive-20261101.md).
