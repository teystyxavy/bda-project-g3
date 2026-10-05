"""Add frozen-model queues to a baseline snapshot without retraining or replacing BQ2."""
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pyarrow.dataset as ds

from .analytics import ranking_metrics
from .dashboard import records
from .model import SavedRanker, digest, rank, read_split


def export_model_dashboard(scored, baseline_path, model_dir, output, assets):
    output = Path(output)
    if output.exists():
        raise ValueError('Use a fresh output directory')
    snapshot = json.loads(Path(baseline_path).read_text(encoding='utf-8'))
    ranker = SavedRanker(model_dir)
    expected = ranker.selection['source_sha256']
    source = next((item['sha256'] for item in snapshot['sources']
                   if item['file'] == 'bq1_scored.parquet'), None)
    if snapshot.get('aml_scope') != 'full' or source != expected or digest(scored) != expected:
        raise ValueError('Model and full-population snapshot source mismatch')
    if snapshot['full_rows'] != ranker.report['total_rows']:
        raise ValueError('Model and snapshot population mismatch')
    dataset = ds.dataset(scored, format='parquet')
    views, queues = {}, {}
    for period in ['validation', 'test', 'tail']:
        frame = read_split(dataset, period)
        scores = ranker.predict(frame)
        model_metrics = ranking_metrics(rank(frame, scores))
        baseline_metrics = ranking_metrics(rank(frame, frame.risk_score.to_numpy()))
        for actual, expected_metrics in [(model_metrics, ranker.report['metrics'][period]['model']),
                (baseline_metrics, ranker.report['metrics'][period]['baseline']),
                (baseline_metrics, snapshot['aml'][period]['metrics'])]:
            for key, value in actual.items():
                if (value is None) != (expected_metrics[key] is None) or (
                        value is not None and not np.isclose(value, expected_metrics[key], rtol=1e-8, atol=1e-10)):
                    raise ValueError(f'Reloaded metric mismatch: {period}/{key}')
        queue = ranker.queue(frame, scores)
        queues[period] = queue
        views[period] = dict(metrics=model_metrics, queue=records(queue))
        print(f'Reloaded frozen model: {period}, {len(frame):,} rows; metrics match', flush=True)
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
    snapshot['buildStatus'] = 'complete'
    output.mkdir(parents=True)
    for period, queue in queues.items():
        queue.to_csv(output / f'model_queue_{period}.csv', index=False)
    for name in ['index.html', 'dashboard.css', 'dashboard.js']:
        shutil.copyfile(Path(assets) / name, output / name)
    (output / 'dashboard.json').write_text(json.dumps(snapshot, allow_nan=False), encoding='utf-8')
    (output / 'validation-diagnostics.json').write_text(json.dumps(snapshot['diagnostics'], indent=2), encoding='utf-8')
    return snapshot
