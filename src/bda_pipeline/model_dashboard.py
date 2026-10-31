"""Add frozen-model queues to a baseline snapshot without retraining or replacing BQ2.

Where this sits in the pipeline
-------------------------------
``batch.export_verified_batch`` produces a baseline ``dashboard.json`` from the
transparent rule score (BQ1) and corridor totals (BQ2). Separately, an
XGBoost ranker is trained and frozen (``model.compare_models`` / SageMaker).
This module adds a ``model`` section to that baseline snapshot so the
dashboard can show the model's top-100 review queue next to the rule queue.

It never retrains, re-tunes or promotes anything, and it leaves the BQ2
treasury numbers untouched. Before writing anything it re-computes the
evaluation metrics and requires them to match the numbers stored when the
model was frozen, which proves the reloaded model and data are the same ones.

Run by ``scripts/dashboard/build-model-dashboard.py --scored ... --baseline
... --model-dir ... --output ... [--cloud-verification ...]``.

Reads
    * ``scored``: ``bq1_scored.parquet`` (rule-scored full population, with labels).
    * ``baseline_path``: the baseline ``dashboard.json``.
    * ``model_dir``: frozen model folder (``comparison.json``,
      ``selection.json``, ``features.json``, ``*.ubj``).
    * optional ``cloud_verification``: ``verification.json`` from
      ``cloud_scores.verify_full_scores``; when given, the dashboard uses the
      verified AWS Glue scores instead of scoring locally.

Writes (into a fresh ``output`` folder)
    ``dashboard.json``, ``validation-diagnostics.json``,
    ``model_queue_<period>.csv`` and copies of the dashboard HTML/CSS/JS.
"""
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pyarrow.dataset as ds

from .analytics import ranking_metrics
from .dashboard import records
from .model import SavedRanker, digest, rank, read_split
from .splits import eval_names


def export_model_dashboard(scored, baseline_path, model_dir, output, assets, cloud_verification=None):
    """Build a dashboard snapshot that contains both the rule and model queues.

    Args:
        scored: path to ``bq1_scored.parquet``; its SHA-256 must equal the
            source hash recorded when the model was selected.
        baseline_path: baseline ``dashboard.json`` (must be ``aml_scope='full'``).
        model_dir: frozen model directory loaded via ``SavedRanker`` (which
            itself verifies every artifact hash).
        output: new directory to write; must not already exist.
        assets: folder with ``index.html``, ``dashboard.css``, ``dashboard.js``.
        cloud_verification: optional path to a passed cloud verification
            report; switches score source from local model to cloud scores.

    Returns:
        The updated snapshot dict (also written to ``output/dashboard.json``).

    Raises:
        ValueError: if the snapshot, scored file and model disagree about the
        source data or population size, if a source row index is outside the
        verified cloud population, or if any reloaded metric differs from the
        stored value.
    """
    output = Path(output)
    if output.exists():
        raise ValueError('Use a fresh output directory')
    snapshot = json.loads(Path(baseline_path).read_text(encoding='utf-8'))
    ranker = SavedRanker(model_dir)
    # Three-way identity check: the hash the model was trained on, the hash
    # recorded in the baseline snapshot's sources list, and the hash of the
    # scored file passed in now must all be the same.
    expected = ranker.selection['source_sha256']
    source = next((item['sha256'] for item in snapshot['sources']
                   if item['file'] == 'bq1_scored.parquet'), None)
    if snapshot.get('aml_scope') != 'full' or source != expected or digest(scored) != expected:
        raise ValueError('Model and full-population snapshot source mismatch')
    if snapshot['full_rows'] != ranker.report['total_rows']:
        raise ValueError('Model and snapshot population mismatch')
    dataset = ds.dataset(scored, format='parquet')
    cloud_scores, cloud_report = None, None
    if cloud_verification is not None:
        # Imported lazily so the local-only path does not need this module.
        from .cloud_scores import load_verified_scores
        cloud_scores, cloud_report = load_verified_scores(cloud_verification, expected,
                            ranker.selection['selected']['sha256'], snapshot['full_rows'])
    views, queues = {}, {}
    # Only the evaluation periods get queues; training rows are excluded so
    # the model is never shown ranking data it learned from.
    for period in eval_names(ranker.splits):
        frame = read_split(dataset, period, ranker.splits)
        if cloud_scores is None:
            # Local path: score this period with the frozen model.
            scores = ranker.predict(frame)
        else:
            # Cloud path: look up each row's verified Glue score by its
            # original CSV row number.
            indices = frame.source_row_index.to_numpy(dtype='int64')
            if ((indices < 0) | (indices >= len(cloud_scores))).any():
                raise ValueError('Dashboard source index outside verified cloud population')
            scores = cloud_scores[indices]
        model_metrics = ranking_metrics(rank(frame, scores))
        baseline_metrics = ranking_metrics(rank(frame, frame.risk_score.to_numpy()))
        # Reproducibility gate: model metrics must equal those stored in
        # comparison.json, and baseline metrics must equal both
        # comparison.json and the baseline snapshot. Missing values (None)
        # must line up too. Tolerances only absorb float rounding.
        for actual, expected_metrics in [(model_metrics, ranker.report['metrics'][period]['model']),
                (baseline_metrics, ranker.report['metrics'][period]['baseline']),
                (baseline_metrics, snapshot['aml'][period]['metrics'])]:
            for key, value in actual.items():
                if (value is None) != (expected_metrics[key] is None) or (
                        value is not None and not np.isclose(value, expected_metrics[key], rtol=1e-8, atol=1e-10)):
                    raise ValueError(f'Reloaded metric mismatch: {period}/{key}')
        # Top-100 model queue with TreeSHAP "evidence" text per row.
        queue = ranker.queue(frame, scores)
        queues[period] = queue
        views[period] = dict(metrics=model_metrics, queue=records(queue))
        print(f'{"Verified cloud scores" if cloud_report else "Reloaded frozen model"}: {period}, {len(frame):,} rows; metrics match', flush=True)
    # The descriptive strings below are shown on the dashboard so viewers do
    # not over-read the model: the score ranks but is not a probability, and
    # SHAP contributions explain the model, not the real-world cause.
    snapshot['model'] = dict(name=ranker.selection['selected']['name'], views=views,
                            selection_policy=ranker.selection['selection_policy'],
                            validation_gate_passed=ranker.selection['validation_gate_passed'],
                            scope='Same full-population evaluation periods; training excluded from queues.',
                            score_definition='Weighted logistic ranking score, not a calibrated probability.',
                            explanation_definition='Top three signed TreeSHAP margin contributions, not causal evidence.',
                            provenance=dict(model_sha256=ranker.selection['selected']['sha256'],
                              comparison_sha256=digest(Path(model_dir) / 'comparison.json'),
                              selection_sha256=ranker.report['selection_sha256'],
                              feature_schema_sha256=ranker.report['feature_schema_sha256'],
                              source_sha256=expected, baseline_snapshot_sha256=digest(baseline_path)))
    snapshot['generated_at'] = datetime.now(timezone.utc).isoformat()
    snapshot['model']['inference_source'] = 'aws-glue' if cloud_report else 'local-frozen-model'
    if cloud_report:
        # Record which Glue run and which verified cache the scores came from.
        snapshot['model']['provenance']['cloud_job_run_id'] = cloud_report['job_run_id']
        snapshot['model']['provenance']['cloud_verification_sha256'] = digest(cloud_verification)
        snapshot['model']['provenance']['cloud_score_cache_sha256'] = cloud_report['score_cache']['sha256']
        snapshot['model']['explanation_definition'] = 'Reconstructed locally: top three signed TreeSHAP margin contributions, not causal evidence.'
    # Tells dashboard.js that the model section is present and final.
    snapshot['buildStatus'] = 'complete'
    output.mkdir(parents=True)
    for period, queue in queues.items():
        queue.to_csv(output / f'model_queue_{period}.csv', index=False)
    for name in ['index.html', 'dashboard.css', 'dashboard.js']:
        shutil.copyfile(Path(assets) / name, output / name)
    (output / 'dashboard.json').write_text(json.dumps(snapshot, allow_nan=False), encoding='utf-8')
    (output / 'validation-diagnostics.json').write_text(json.dumps(snapshot['diagnostics'], indent=2), encoding='utf-8')
    return snapshot
