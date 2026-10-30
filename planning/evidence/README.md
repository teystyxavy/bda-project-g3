# Verification records

The latest fully inspected HI-Small end-to-end acceptance is
[pipeline-optimisation-result-20261006.json](pipeline-optimisation-result-20261006.json).
It links measurements, business-output parity, score/corridor verification,
stage reuse, candidate training and infrastructure drift checks.

Other JSON records preserve preparation, deployment and acceptance milestones.
Their status fields distinguish local/read-only validation from actual AWS runs.
Detailed data, container outputs and full logs are excluded from Git under `output/`.

These records were moved without changing their contents. Path/hash fields retain
the original verification-time paths; documentation now resolves them through the
[project index](../README.md). Account IDs, resource names, digests and public URLs
are identifiers, not credentials. Secrets and private Terraform state are excluded.

[HI-Medium publication verification](hi-medium-result-20261030.json) records the
31,898,238-row public snapshot and explicitly lists pending private AWS checks.
