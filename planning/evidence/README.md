# Verification records

Sanitized milestone summaries. Status fields distinguish local or read-only checks
from actual AWS runs. Detailed data, container outputs and full logs stay in the
ignored `output/` folder.

## Latest

| Milestone | Record |
|---|---|
| HI-Large delta1 run: lean export, publication and Step Functions resume (8 Nov) | [hi-large-delta1-result-20261108.json](hi-large-delta1-result-20261108.json) |
| Full HI-Medium dashboard ingestion deployed (8 Nov) | [full-medium-dashboard-deployment-20261108.json](full-medium-dashboard-deployment-20261108.json) |
| Streaming delta1 replay, 984 events reconciled (7 Nov) | [streaming-delta1-result-20261107.json](streaming-delta1-result-20261107.json) |
| delta1 promotion (7 Nov) | [delta1-shared-promotion-20261107.json](delta1-shared-promotion-20261107.json) |
| HI-Large deployment and run start (7 Nov) | [hi-large-delta1-deployment-20261107.json](hi-large-delta1-deployment-20261107.json), [hi-large-delta1-run-20261107.json](hi-large-delta1-run-20261107.json) |
| HI-Medium delta1 run (7 Nov) | [hi-medium-delta1-run-20261107.json](hi-medium-delta1-run-20261107.json) |
| BQ2 mule-network deployment (7 Nov) | [bq2-deployment-20261107.json](bq2-deployment-20261107.json) |

Records with `"status": "RUNNING"` were captured at launch; the later result
records above give the outcome.

## Earlier

The latest fully inspected HI-Small end-to-end acceptance is
[pipeline-optimisation-result-20261006.json](pipeline-optimisation-result-20261006.json).
It links measurements, business-output parity, score and corridor verification,
stage reuse, candidate training and infrastructure drift checks.
[HI-Medium publication verification](hi-medium-result-20261030.json) records the
31,898,238-row public snapshot.

The other JSON records preserve preparation, deployment and acceptance milestones
from October and early November. They were moved without changing their contents,
so their path and hash fields keep the layout at verification time. Account IDs,
resource names, digests and public URLs are identifiers, not credentials. Secrets
and private Terraform state are excluded.
