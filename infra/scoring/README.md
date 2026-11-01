# Retired standalone scoring stack

Retired 1 November 2026 during the user-authorized AWS cleanup. The current
`is459-g3-ingest-score` job in `infra/ingestion/` performs frozen-model scoring.

Terraform removed the obsolete `is459-g3-score-frozen` job, its two dedicated
roles and policies, failure rule/target and two log groups (nine resources).
The shared state contains no managed resources and the shared handoff inputs
set `provision_scoring=false`. Keep this flag disabled when checking the retired
stack. The source remains available as a historical implementation.

See [cleanup record](../../planning/runbooks/aws-cleanup-20261101.md).
