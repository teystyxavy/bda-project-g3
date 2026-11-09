# Dashboard exploration and read-only pipeline status

> **Current, 9 November 2026.** The live dashboard is UI release
> `ui-main-20261109-janelle` with HI-Large data (179,702,229 rows scored by delta1).
> Tabs: BQ1 review queue, BQ2 mule networks (rings, hubs, loops; one oversized
> 26,370-account component is withheld with a notice), **Incoming AML** (streaming
> replay snapshot) and **Ingestion** (status and run controls, including cache-only HI-Large). The
> coverage numbers below describe the 30 Oct HI-Medium release.

The 30 October coverage update uses UI release `medium-coverage-20261030` and
source pipeline run `medium-full-20261030-v2`. Its fixed coverage summary shows
**31,898,238 scored = 18,893,741 evaluated + 13,004,497 excluded from evaluation**.
The excluded rows are Sep 1–6, the original HI-Small model's training-reference
dates. They still received scores. The model was reused for Medium without
retraining; possible synthetic-population overlap limits generalisation claims.
The summary stays visible above period/population filters and reconciles all
four date windows in an expandable table. The selected-period metrics and queues
continue to cover only the selected population. The publication preserves the
verified snapshot and starts no new batch or training jobs. See
[coverage publication evidence](../evidence/dashboard-scoring-coverage-20261030.json).

The earlier 7 October UI release `insights-ui-20261007-v1` used the
existing 5,078,345-row verified snapshot and frozen model. Status-stack deployment,
worker update and public asset verification are recorded in
[deployment evidence](../evidence/dashboard-status-exploration-result-20261007.json).
Final browser visual acceptance is unverified because the browser tool failed to
initialize; the captured live status payload passed the dashboard render harness.

## Business views

BQ1 separates investigation from retrospective model evaluation. Choose review
capacity 25, 50, 100 or 250 for a whole evaluation period. Payment, currency and the
five leading sender-bank presets independently rank the complete selected
population; their bounded exports support capacities through 100. Search, amount,
payment and currency row filters narrow the exported queue only. The UI states
this distinction. Exports contain the shown rows with their original ranks.

Daily review selections, score distributions, precision-recall curves and
capacity comparisons follow the selected scorer and population. Average precision
uses every tied-score boundary; curve coordinates are bounded for display. The
existing top-100 metrics remain unchanged. Synthetic labels appear only in
evaluation aggregates, never investigation records.

The evidence drawer shows sender/receiver identifiers, observed features and
signed model contributions for each period's global model top 250. Other preset
records explicitly disclose unavailable contributions. Latest-eight preceding
outgoing account events are supplied for the global model/rule top 250, joined by
bank plus account and bounded before the selected transaction. Original payment
amount is unavailable in this reference export; currency and USD-equivalent
valuation remain visible. No new model or threshold is introduced.

BQ2 adds a directed heatmap whose twelve leading endpoints plus Other banks
reconcile to all volume. Complete daily histories cover the union of the top 30
all-period and top 15 daily bank pairs (203 in the verified full snapshot).
Corridor drill-down includes volume, share, transaction counts, average amount and
all-period top-ten currency composition. Share movers compare every bank pair;
the all-period comparison splits the observed dates into two halves, while a
single-date comparison uses the preceding observed date. The first day has no
invented comparison. Top-one/top-five share trends and currency-flow ribbons keep
treasury volume separate from AML suspicion.

## Rebuild and publish

`scripts/dashboard/build-exploration.py` enriches an existing verified snapshot
using its matching scored reference, score-verification proof, frozen model and
joint daily corridor mart. It rejects source/model mismatches. Run with `--help`
for required arguments. It makes no AWS calls.

`scripts/dashboard/prepare-dashboard-release.py` packages the four public assets,
rejecting private paths, credential-like content and investigation label fields.
`scripts/dashboard/publish-exploration.py` publishes only to the existing Group 3
hosting bucket and distribution after explicit user authorization. Supply a fresh
release ID, matching expected-current pointer and fresh evidence directory. It
preserves old releases, verifies uploaded hashes, rejects active pipeline runs and
conditionally switches the current pointer. The UI release ID is distinct from
the source pipeline run ID; data timestamps continue to identify data publication.

The processing image bundles these same UI assets and the enriched exporter. An
image update must retain matching model/deployment metadata. When replacing a
deployment prefix, retain its immutable objects and private Terraform state
backup; never apply an artifact-delete plan merely to update the UI. Verify the
new saved plan's exact resource actions before applying. No paid ingestion run is
needed to publish this existing verified snapshot.

## Public Ingestion status

Follow [the status-stack deployment steps](../../infra/dashboard-status/README.md).
The public feed is read-only and refreshes while the tab is visible. It cannot
start or cancel jobs. Operator controls and AWS identity sign-in use the separate
[deployed control stack](../../infra/dashboard-control/README.md). `status-config.json` is a public endpoint configuration outside
the four-asset release; pipeline publication preserves it.

## Verification recorded before deployment

- Requested Python status/API tests: 38 passed.
- Dashboard controls tests: 13 passed.
- Full non-Spark Python suite: 208 passed, 1 skipped.
- Status Terraform validation and mocked test: passed (1 test).
- Status AWS plan: 10 additions, 0 changes, 0 deletions.
- Full-snapshot checks: 462 capacity/metric checks, 19 heatmaps reconciled,
  original global top-100 metrics unchanged, investigation labels absent.
- Offline container export smoke: passed, network disabled.
