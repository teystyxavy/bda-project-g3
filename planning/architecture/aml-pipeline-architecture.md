# Implemented batch architecture

> **Superseded, 9 November 2026.** This guide records an October milestone. For the
> current architecture see [`docs/is459-g3-aml-pipeline-aws.drawio`](../../docs/is459-g3-aml-pipeline-aws.drawio)
> and the [architecture guide](README.md).

Editable source: [aml-pipeline-architecture.drawio](aml-pipeline-architecture.drawio).
The two pages cover the two core business questions. Status reflects the verified
6 October 2026 milestones, not a new live cloud check. Original proposal sources
were removed from the repository (see the archive note in `../README.md`).

## Page 1: BQ1 investigation ranking

1. Local Kaggle and FX preparation produces validated inputs; explicitly approved
   uploads land them in the private encrypted S3 raw area.
2. AWS Glue PySpark curation reads duplicate Account columns by position, joins
   metadata using bank/account composite keys and preserves string identifiers.
   It computes strictly preceding behaviour and writes Parquet by transaction date.
3. Local XGBoost training uses chronological train/validation/test periods.
   The validation-selected depth-5 model is frozen before test evaluation.
   The reviewed release carries its UBJ model, feature schema and hashed manifest.
4. AWS Glue inference consumes the verified curated population and frozen model.
   All 5,078,345 rows reconcile with the reference.
5. Local acceptance, signed TreeSHAP explanations and dashboard export produce
   the static release. Queue rows omit labels. The private S3 dashboard origin
   serves CloudFront via Origin Access Control; viewers use public HTTPS.
6. Compliance compares model/rule ranking and reviews individual explanations.
   The main test top 100 finds 44 labelled positives for the model versus zero
   for rules. This is an exploratory synthetic-data result, not a calibrated
   probability or causal explanation.

## Page 2: BQ2 treasury concentration

1. The same reconciled Parquet supplies historical same-day `amount_usd`.
2. Glue catalog metadata and date partition projection describe the files.
   The diagram's catalog arrow denotes the query path; it does not imply the
   catalog stores or copies the Parquet itself.
3. Athena computes directed bank/currency daily corridors, shares and HHI.
   All 1,082,185 detailed daily rows passed acceptance for the full population.
4. Local reconciliation/export supplies the static corridor dashboard using
   the same private-origin hosting path.
5. Treasury explores bank/currency volume dependence and daily variation.
   Dashboard bank-pair concentration aggregates currencies and therefore has a
   different grain from the detailed Athena rows. High volume does not itself
   establish suspiciousness.

## Services, local components and boundaries

| Component | Purpose |
|---|---|
| Local ingestion and FX preparation | Reuse Kaggle files, preserve FX source/observation metadata |
| S3 raw/curated/model storage | Encrypted inputs, date-partitioned analytics and frozen release |
| Glue ETL and scoring | Distributed curation and frozen inference |
| Glue catalog | Explicit schema and date partition projection, with no crawlers |
| Athena | Treasury SELECT analysis |
| Local training/reconciliation/export | Model selection, acceptance and static visualisation build |
| Private S3 dashboard origin and CloudFront | Public dashboard with origin access restricted to its distribution |

The diagrams use official `mxgraph.aws4` draw.io stencils. All service names
were verified against the AWS4 library bundled in the signed official
[draw.io 31.7.0 portable release](https://github.com/jgraph/drawio-desktop/releases/tag/v31.7.0).
The archive SHA256 matched the release metadata. Outlined boxes
identify local steps and audiences. Lane boundaries group editable children.
Solid arrows show implemented artifact/query flow; dashed training input denotes
the separate local frozen-model preparation path. Two diagonal input edges cross
the empty gap between lanes to keep them clear of unrelated nodes.

Additional encryption, least-privilege IAM, monitoring and budget controls are
documented in [deployment status](../aws-deployment-status.md); this view focuses
on the business data paths. Optional enhancements are in
[aml-pipeline-aws-icons.drawio](aml-pipeline-aws-icons.drawio).

Rebuild both files locally with:

```powershell
output/ml-venv/Scripts/python.exe scripts/architecture/build-architecture-diagrams.py
```

The generator performs XML/ID/parent/edge-reference validation and preserves the
first pre-update sources. It makes no AWS calls and does not modify the proposal
presentation. Keep the generator and companion guides consistent when changing
the diagram design.

All four PNG pages were exported locally and visually reviewed. Copies under
`planning/architecture/exports/` embed their diagram XML and remain editable in
draw.io. No further third-party upload is required. Use
`scripts/architecture/export-architecture.ps1` with the reviewed portable exporter for local
exports. CLI page numbers are one-based in the reviewed release.
