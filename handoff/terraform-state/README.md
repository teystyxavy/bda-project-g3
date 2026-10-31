# Refreshed Terraform state handoff — 31 October 2026

All 12 stacks have current state in the versioned, encrypted S3 bucket
`is459-g3-tfstate-588738581042`, under `<stack>/terraform.tfstate`. The
`foundation` key belongs to `infra/terraform`. S3 is authoritative; files here are
reviewed recovery snapshots. Use the [shared-backend guide](../../infra/tfstate-bootstrap/README.md)
to connect from another machine and download deployed inputs/artifacts.

The latest snapshots were refreshed with each stack's actual Terraform
configuration and deployed inputs. Saved refresh-only plans were checked to
contain no resource modification actions before applying them to isolated local
states. Subsequent provider reads found no resource drift. Publication used
Terraform's S3 backend and DynamoDB locking, without force-overwriting another
state. All resource attributes and outputs were read back and compared. Newly
populated S3 keys use backend-generated lineage/serial histories; foundation
retains its existing shared history.

The missing bootstrap state was recovered by importing six existing resources.
The streaming refresh removed the absent Kinesis stream and Lambda event mapping
from state; the live stream remains parked. API Gateway deployment IDs and
provider readback metadata are now captured. No application resources were
created, modified or deleted, and no paid jobs were started.

[The final verification report](shared-backend-verification-20261031.json) records
checks, S3 object versions and checksums. `manifest.json` lists snapshots and
historical backups. `live-verification-20261031.json` remains as historical
evidence from before the refresh/cutover. Files under `backups/` are historical
and must not be treated as current.

## Pending application changes

State is current, but the repository also contains undeployed application changes.
Normal plans with deployed inputs propose three updates:

- Foundation: the source hash of the Glue curate script.
- EventBridge trigger: the Step Functions definition.
- Orchestration: the earlier batch Step Functions definition.

These are separate from state refresh/backend publication and were not applied.
Review them before infrastructure deployment. The other nine stacks produce no
resource changes with the original deployed input paths. Downloading unchanged
artifacts to a different machine can additionally produce local source/filename
path updates and dependent JSON formatting differences; artifact hashes are
checked by the input helper. Always review a normal plan
before applying; state verification does not test application behavior or discover
all unmanaged resources in the account.

## Recovery and data handling

Retrieve the latest state from S3 with its current lineage/serial before planning
or restoring anything. Never push a historical backup over shared state.
Coordinate operators and preserve S3 version history.

Credential-pattern and secret-field checks, including embedded JSON, found no
credentials or secret values in these reviewed snapshots. They contain AWS
identifiers, configuration, local artifact paths and notification email settings.
Future snapshots require separate review. Global state ignore rules remain
enabled; only reviewed handoff files are explicitly committed. Credentials,
private input files and provider caches remain outside Git.
