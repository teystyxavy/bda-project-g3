"""Local chronological XGBoost comparison for the BQ1 review queue: trains on curated features, compares to the rule baseline with identical metrics and explains alerts with TreeSHAP.
Splits come from ``splits.py`` (recorded in the snapshot) and the selection is frozen before test data is read; ``compare_models`` writes its artifacts to a fresh directory."""
import gc
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.dataset as ds

from .analytics import ranking_metrics
from .batch import FEATURES, QUEUE_COLUMNS
from .splits import LEGACY_SPLITS, eval_names

# String columns that are one-hot encoded for the model.
CATEGORICAL = ['payment_format', 'payment_currency', 'receiving_currency']
# Columns read from Parquet; IDs, timestamp, label and baseline score are for ranking and display, not model features.
INPUT_COLUMNS = ['transaction_id', 'source_row_index', 'timestamp', 'from_bank',
                 'from_account', 'to_bank', 'to_account', 'amount_usd_asof',
                 'is_laundering', 'risk_score', 'risk_reasons', *FEATURES, *CATEGORICAL]
# Only tree depth is compared; a tiny, pre-declared search limits overfitting to validation.
CANDIDATES = [{'name': 'hist-depth3', 'max_depth': 3},
              {'name': 'hist-depth5', 'max_depth': 5}]


def digest(path):
    """SHA-256 hex digest of a file, used to verify inputs, models and manifests."""
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def fit_encoder(training):
    """Builds the category vocabulary from the training split only; categories first seen later use the ``<column>_unknown`` flag.
    IDs, labels, dates, same-day ``amount_usd`` and target encoding are excluded to prevent leakage."""
    return {column: sorted(training[column].dropna().unique().tolist())
            for column in CATEGORICAL}


def encode(frame, vocabulary, feature_names=None):
    """Turns transactions into a float32 feature matrix: log1p amount and behaviour counts, self-transfer and currency-mismatch flags, cyclic hour and one-hot categories.
    Raises ValueError on negative/non-finite numbers or missing categories; column order is deterministic, which ``SavedRanker`` relies on."""
    values = {}
    numeric = ['amount_usd_asof', *FEATURES]
    if feature_names is not None:
        # Frozen models retain their original feature contract even when the
        # current pipeline computes additional behaviour features. Never add
        # new inputs to an old model or fabricate missing feature values.
        numeric = [name.removeprefix('log1p_') for name in feature_names if name.startswith('log1p_')]
        if len(numeric) != len(set(numeric)) or set(numeric) - {'amount_usd_asof', *FEATURES}:
            raise ValueError('Unsupported frozen numeric feature schema')
    for column in numeric:
        numbers = frame[column].to_numpy(dtype=np.float64)
        # log1p of a negative number would be NaN (or -inf at -1).
        if not np.isfinite(numbers).all() or (numbers < 0).any():
            raise ValueError(f'Invalid nonnegative numeric feature: {column}')
        values['log1p_' + column] = np.log1p(numbers).astype(np.float32)
    # Same zero-stripping rule as analytics.bank_key ("010" == "10").
    bank = lambda column: frame[column].astype(str).str.lstrip('0').replace('', '0')
    values['is_self_txn'] = (bank('from_bank').eq(bank('to_bank')) &
                             frame.from_account.eq(frame.to_account)).to_numpy(dtype=np.float32)
    values['currency_mismatch'] = frame.payment_currency.ne(frame.receiving_currency).to_numpy(dtype=np.float32)
    hour = frame.timestamp.dt.hour.to_numpy()
    values['hour_sin'] = np.sin(hour * 2 * np.pi / 24).astype(np.float32)
    values['hour_cos'] = np.cos(hour * 2 * np.pi / 24).astype(np.float32)
    for column in CATEGORICAL:
        if frame[column].isna().any():
            raise ValueError(f'Missing categorical feature: {column}')
        for index, category in enumerate(vocabulary[column]):
            values[f'{column}_{index}'] = frame[column].eq(category).to_numpy(dtype=np.float32)
        # Catch-all for categories never seen in training.
        values[column + '_unknown'] = (~frame[column].isin(vocabulary[column])).to_numpy(dtype=np.float32)
    return pd.DataFrame(values, index=frame.index)


def rank(frame, scores):
    """Attaches model scores as ``risk_score`` and sorts into the same deterministic queue order as ``analytics.score_baseline``.
    Raises ValueError if the score count differs or any score is NaN/inf."""
    if len(scores) != len(frame) or not np.isfinite(scores).all():
        raise ValueError('Invalid prediction scores')
    return frame.assign(risk_score=scores).sort_values(
        ['risk_score', 'timestamp', 'source_row_index'],
        ascending=[False, True, True], kind='stable')


def choose_candidate(candidates):
    """Preregistered selection: highest validation AP, then precision@100 (None counts as 0), then name (the simpler model wins ties)."""
    return sorted(candidates, key=lambda c: (-(c['metrics']['average_precision'] or 0),
                  -(c['metrics']['precision_at_k'] or 0), c['name']))[0]


def read_split(dataset, name, splits):
    """Loads one chronological split of ``splits`` (``(start, end)`` dates), pushing the timestamp filter down to pyarrow and reading only ``INPUT_COLUMNS``.
    Raises ValueError if the split is empty, has non-0/1 labels or has duplicate ``transaction_id``."""
    start, end = splits[name]
    condition = ((ds.field('timestamp') >= pd.Timestamp(start)) &
                 (ds.field('timestamp') < pd.Timestamp(end)))
    frame = dataset.to_table(columns=INPUT_COLUMNS, filter=condition).to_pandas()
    if frame.empty or not frame.is_laundering.isin([0, 1]).all():
        raise ValueError(f'Empty split or invalid labels: {name}')
    if frame.transaction_id.duplicated().any():
        raise ValueError(f'Duplicate transaction IDs: {name}')
    return frame


def explain_queue(model, frame, scores, vocabulary, k=100):
    """Builds the top-``k`` review queue with the three largest exact TreeSHAP contributions per alert (model margins, not causal claims).
    Raises ValueError if the contributions do not reproduce the model scores."""
    import xgboost as xgb
    # Rank only keys and scores, then copy feature columns for the displayed rows (avoids copying millions of rows).
    ordering = frame[['timestamp', 'source_row_index']].reset_index(drop=True)
    selected = rank(ordering, scores).head(k)
    # selected.index holds positions into frame (thanks to reset_index above).
    queue = frame.iloc[selected.index].copy()
    queue['risk_score'] = selected.risk_score.to_numpy()
    matrix = encode(queue, vocabulary, model.feature_names)
    # Shape: (k, n_features + 1); last column is the bias (expected margin).
    contributions = model.predict(xgb.DMatrix(matrix), pred_contribs=True)
    margin = contributions.sum(axis=1)
    if not np.allclose(1 / (1 + np.exp(-margin)), queue.risk_score, rtol=1e-4, atol=1e-6):
        raise ValueError('Explanation/prediction mismatch')
    names = matrix.columns.tolist()
    reasons = []
    # Exclude the bias column, then take the 3 largest contributions per row.
    for row in contributions[:, :-1]:
        indices = np.argsort(-row, kind='stable')[:3]
        reasons.append('; '.join(f'{names[i]}: {row[i]:+.4f} margin' for i in indices))
    queue['risk_reasons'] = reasons
    result = queue[QUEUE_COLUMNS].rename(columns={'risk_score': 'model_score',
                                                 'risk_reasons': 'model_evidence'})
    result['explanation_bias_margin'] = contributions[:, -1]
    result['explanation_total_margin'] = margin
    return result


class SavedRanker:
    """Reloads a frozen model after verifying the recorded hashes, the frozen-before-test selection, a safe ``*.ubj`` filename and the feature schema.
    Any failure raises ValueError, so the scoring model is exactly the evaluated one."""
    def __init__(self, directory, threads=4):
        """Verifies and loads the artifacts in ``directory`` (see class docstring)."""
        # Imported lazily so modules that only need helpers do not require xgboost.
        import xgboost as xgb
        directory = Path(directory)
        self.report = json.loads((directory / 'comparison.json').read_text(encoding='utf-8'))
        for name, key in [('selection.json', 'selection_sha256'),
                          ('features.json', 'feature_schema_sha256')]:
            if digest(directory / name) != self.report[key]:
                raise ValueError(f'Artifact hash mismatch: {name}')
        self.selection = json.loads((directory / 'selection.json').read_text(encoding='utf-8'))
        self.splits = self.report.get('splits') or LEGACY_SPLITS
        if self.selection != self.report['selection'] or not self.selection['frozen_before_test']:
            raise ValueError('Frozen selection mismatch')
        selected = self.selection['selected']
        name = selected['model_file']
        # Reject names like "../x.ubj" or "sub/x.ubj" (path traversal).
        if Path(name).name != name or Path(name).suffix != '.ubj':
            raise ValueError('Invalid model filename')
        if digest(directory / name) != selected['sha256']:
            raise ValueError('Model hash mismatch')
        schema = json.loads((directory / 'features.json').read_text(encoding='utf-8'))
        self.vocabulary, self.feature_names = schema['vocabulary'], schema['feature_names']
        self.model = xgb.Booster(model_file=directory / name)
        self.model.set_param({'nthread': threads})
        self.threads = threads
        if self.model.feature_names != self.feature_names:
            raise ValueError('Model feature schema mismatch')

    def predict(self, frame):
        """Encodes ``frame`` and returns model scores (0..1 ranking scores, not calibrated probabilities).
        Raises ValueError if the encoded columns differ from the saved schema."""
        import xgboost as xgb
        matrix = encode(frame, self.vocabulary, self.feature_names)
        if matrix.columns.tolist() != self.feature_names:
            raise ValueError('Encoded feature schema mismatch')
        return self.model.predict(xgb.DMatrix(matrix, nthread=self.threads))

    def queue(self, frame, scores, k=100):
        """Like ``explain_queue`` but rewrites feature names in ``model_evidence`` into reviewer-readable labels."""
        queue = explain_queue(self.model, frame, scores, self.vocabulary, k=k)
        # Map index-based one-hot names back to "column=value".
        aliases = {f'{column}_{index}': f'{column}={value}'
                   for column in CATEGORICAL
                   for index, value in enumerate(self.vocabulary[column])}
        aliases.update({'log1p_amount_usd_asof': 'Prior-day USD amount (log)',
                        'is_self_txn': 'Self-transfer', 'currency_mismatch': 'Currency mismatch',
                        'hour_sin': 'Transaction hour (cyclic sine)',
                        'hour_cos': 'Transaction hour (cyclic cosine)'})
        # Labels listed in the same order as batch.FEATURES.
        aliases.update({f'log1p_{name}': label for name, label in zip(FEATURES,
                       ['Prior-hour transaction count (log)', 'Prior-day transaction count (log)',
                        'Prior-week transaction count (log)', 'Prior-day counterparties (log)',
                        'Prior-week counterparties (log)',
                        'Prior-hour incoming count (log)', 'Prior-day incoming count (log)',
                        'Prior-week incoming count (log)', 'Prior-day distinct senders (log)',
                        'Prior-week distinct senders (log)'])})
        # Split each "name: +x margin" part at the first ":" and swap the name for its alias.
        queue['model_evidence'] = queue.model_evidence.map(lambda evidence: '; '.join(
            aliases.get(part.split(':', 1)[0], part.split(':', 1)[0]) + ':' + part.split(':', 1)[1]
            for part in evidence.split('; ')))
        return queue


def compare_models(scored, snapshot_path, output, rounds=120, threads=4):
    """Trains the candidates, picks one on validation, freezes ``selection.json`` and ``features.json`` before reading test data, then evaluates every period against the baseline.
    Writes to a fresh ``output`` directory, returns the ``comparison.json`` report and raises ValueError on any input or coverage failure."""
    import xgboost as xgb
    if rounds <= 0 or threads <= 0:
        raise ValueError('Rounds and threads must be positive')
    output = Path(output)
    if output.exists():
        raise ValueError('Use a fresh output directory')
    snapshot = json.loads(Path(snapshot_path).read_text(encoding='utf-8'))
    if snapshot.get('aml_scope') != 'full':
        raise ValueError('Full-population snapshot required')
    expected_hash = next((item['sha256'] for item in snapshot['sources']
                          if item['file'] == 'bq1_scored.parquet'), None)
    actual_hash = digest(scored)
    if actual_hash != expected_hash:
        raise ValueError('Scored input hash differs from verified export')
    splits = snapshot.get('splits') or LEGACY_SPLITS
    dataset = ds.dataset(scored, format='parquet')
    if dataset.count_rows() != snapshot['full_rows']:
        raise ValueError('Scored input row count mismatch')
    output.mkdir(parents=True)
    training, validation = read_split(dataset, 'train', splits), read_split(dataset, 'validation', splits)
    vocabulary = fit_encoder(training)
    xtrain, xvalid = encode(training, vocabulary), encode(validation, vocabulary)
    positives = int(training.is_laundering.sum())
    negatives = len(training) - positives
    # Training needs both classes; validation needs positives or AP is undefined.
    if not positives or not negatives or not validation.is_laundering.sum():
        raise ValueError('Training needs both classes and validation needs positives')
    train_counts = dict(rows=len(training), positives=positives)
    feature_names = xtrain.columns.tolist()
    # QuantileDMatrix pre-bins features to save memory; ref=dtrain reuses the training bin edges for validation.
    dtrain = xgb.QuantileDMatrix(xtrain, label=training.is_laundering, nthread=threads)
    dvalid = xgb.QuantileDMatrix(xvalid, label=validation.is_laundering, ref=dtrain, nthread=threads)
    del training, xtrain
    gc.collect()
    # aucpr suits rare positives; scale_pos_weight up-weights laundering rows, so scores are not calibrated probabilities.
    params = dict(objective='binary:logistic', tree_method='hist', device='cpu',
                  eval_metric='aucpr', eta=.1, min_child_weight=10, subsample=1,
                  colsample_bytree=1, seed=459, nthread=threads,
                  scale_pos_weight=negatives/positives)
    candidates = []
    for candidate in CANDIDATES:
        print(f"Training {candidate['name']} on {train_counts['rows']:,} rows", flush=True)
        # Stop if validation AUC-PR has not improved for 20 rounds; log every 20.
        model = xgb.train({**params, 'max_depth': candidate['max_depth']}, dtrain,
                          num_boost_round=rounds, evals=[(dvalid, 'validation')],
                          early_stopping_rounds=20, verbose_eval=20)
        # Slice explicitly: later predictions and saved artifacts use selected trees only.
        model = model[:model.best_iteration + 1]
        predictions = model.predict(xgb.DMatrix(xvalid, nthread=threads))
        metrics = ranking_metrics(rank(validation, predictions))
        path = output / (candidate['name'] + '.ubj')
        model.save_model(path)
        candidates.append(dict(**candidate, trees=model.num_boosted_rounds(),
                               metrics=metrics, model_file=path.name, sha256=digest(path)))
        print(json.dumps(candidates[-1]), flush=True)
    selected = choose_candidate(candidates)
    # The rule baseline's risk_score (already in the input) ranked the same way.
    baseline_validation = ranking_metrics(rank(validation, validation.risk_score.to_numpy()))
    # The gate is recorded, not enforced: nothing is deployed automatically.
    eligible = (selected['metrics']['average_precision'] > baseline_validation['average_precision']
                and selected['metrics']['precision_at_k'] >= baseline_validation['precision_at_k'])
    selection = dict(selected=selected, candidates=candidates, baseline_validation=baseline_validation,
                     selection_policy='Highest validation average precision; then precision@100; then name.',
                     validation_gate_passed=eligible,
                     gate='AP improves and precision@100 is not worse than baseline; no automatic deployment.',
                     frozen_before_test=True, source_sha256=actual_hash,
                     generated_at=datetime.now(timezone.utc).isoformat())
    # Persist the choice before loading test labels or computing any test metrics.
    (output / 'selection.json').write_text(json.dumps(selection, indent=2), encoding='utf-8')
    (output / 'features.json').write_text(json.dumps(dict(vocabulary=vocabulary,
                feature_names=feature_names), indent=2), encoding='utf-8')
    # Reload from disk so evaluation uses exactly the saved artifact.
    model = xgb.Booster(model_file=output / selected['model_file'])
    model.set_param({'nthread': threads})
    del dtrain, dvalid, xvalid, validation
    gc.collect()
    metrics = {}
    # Running totals for the coverage check below (start with training).
    total_rows = train_counts['rows']
    total_positives = positives
    for name in eval_names(splits):
        frame = read_split(dataset, name, splits)
        predictions = model.predict(xgb.DMatrix(encode(frame, vocabulary), nthread=threads))
        metrics[name] = dict(model=ranking_metrics(rank(frame, predictions)),
                            baseline=ranking_metrics(rank(frame, frame.risk_score.to_numpy())))
        explain_queue(model, frame, predictions, vocabulary).to_csv(
            output / f'model_queue_{name}.csv', index=False)
        total_rows += len(frame)
        total_positives += int(frame.is_laundering.sum())
        print(f'{name}: {json.dumps(metrics[name])}', flush=True)
        del frame, predictions
        gc.collect()
    # The splits must cover every row once and match the snapshot's positives (training positives are added separately).
    if total_rows != snapshot['full_rows'] or total_positives != sum(
            snapshot['aml'][p]['metrics']['positives'] for p in eval_names(splits)) + positives:
        raise ValueError('Split coverage mismatch')
    # Hashes of selection/features let SavedRanker detect later tampering.
    report = dict(selection=selection, metrics=metrics, train=train_counts, splits=splits,
                  total_rows=total_rows, total_positives=total_positives,
                  parameters=params, max_rounds=rounds, xgboost_version=xgb.__version__,
                  feature_names=feature_names, selection_sha256=digest(output / 'selection.json'),
                  feature_schema_sha256=digest(output / 'features.json'),
                  limitations=['Weighted logistic scores are not calibrated probabilities.',
                               'Exploratory synthetic dataset; prior whole-data EDA limits holdout claims.',
                               'Cold-start account history; timestamps have no verified timezone.',
                               'TreeSHAP explains model margins, not causal laundering evidence.',
                               'No model promotion, AWS training, or dashboard replacement.'])
    # allow_nan=False: fail rather than write invalid JSON if a metric is NaN.
    (output / 'comparison.json').write_text(json.dumps(report, indent=2, allow_nan=False), encoding='utf-8')
    return report
