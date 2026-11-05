"""Local chronological XGBoost comparison for the BQ1 review queue: trains on curated features, compares to the rule baseline with identical metrics and explains alerts with TreeSHAP.
Splits come from ``splits.py`` (recorded in the snapshot) and the selection is frozen before test data is read; ``compare_models`` writes its artifacts to a fresh directory."""
import gc
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.compute as pc
import pyarrow.dataset as ds

from .analytics import ranking_metrics
from .batch import FEATURES, QUEUE_COLUMNS
from .splits import LEGACY_SPLITS, SPARSE_FRACTION, eval_names, sparse_tail_start

# Rows per streamed batch: bounds the memory held while training reads a split (about 0.5 GB of features at 2 million rows).
BATCH_ROWS = 2_000_000

# String columns that are one-hot encoded for the model.
CATEGORICAL = ['payment_format', 'payment_currency', 'receiving_currency']
# Columns read from Parquet; IDs, timestamp, label and baseline score are for ranking and display, not model features.
INPUT_COLUMNS = ['transaction_id', 'source_row_index', 'timestamp', 'from_bank',
                 'from_account', 'to_bank', 'to_account', 'amount_usd_asof',
                 'is_laundering', 'risk_score', 'risk_reasons', *FEATURES, *CATEGORICAL]
# A tiny, pre-declared grid limits overfitting to validation. ``weight`` is the multiplier on laundering rows: a number, or
# 'balanced' for negatives/positives (about 1,280 on HI-Small, the setting used before the weight became a candidate).
# Validation-only sweeps found no weight beating 1 and depths 5-8 equivalent; names must be lowercase letters, digits and hyphens.
# The last two candidates add max_delta_step/subsample on top of the winning depth-5/unweighted setting, per XGBoost's
# own tuning guide recommendation for this project's ~1,283:1 class imbalance; any of PARAM_KEYS a candidate sets
# overrides the shared training params below (see compare_models), others keep XGBoost's own defaults.
PARAM_KEYS = ('max_delta_step', 'subsample', 'reg_lambda', 'reg_alpha')
CANDIDATES = [{'name': 'hist-depth5-unweighted', 'max_depth': 5, 'weight': 1},
              {'name': 'hist-depth5-light', 'max_depth': 5, 'weight': 5},
              {'name': 'hist-depth5-balanced', 'max_depth': 5, 'weight': 'balanced'},
              {'name': 'hist-depth5-delta1', 'max_depth': 5, 'weight': 1, 'max_delta_step': 1},
              {'name': 'hist-depth5-delta5-sub08', 'max_depth': 5, 'weight': 1, 'max_delta_step': 5, 'subsample': 0.8}]


def digest(path):
    """SHA-256 hex digest of a file, used to verify inputs, models and manifests."""
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def positive_weight(candidate, positives, negatives):
    """The ``scale_pos_weight`` a candidate trains with: its numeric ``weight``, or negatives/positives for 'balanced'."""
    weight = candidate['weight']
    return negatives / positives if weight == 'balanced' else float(weight)


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


def window_filter(splits, name):
    """The pyarrow timestamp condition for one half-open ``(start, end)`` window of ``splits``."""
    start, end = splits[name]
    return (ds.field('timestamp') >= pd.Timestamp(start)) & (ds.field('timestamp') < pd.Timestamp(end))


def read_split(dataset, name, splits):
    """Loads one chronological split of ``splits`` (``(start, end)`` dates), pushing the timestamp filter down to pyarrow and reading only ``INPUT_COLUMNS``.
    Raises ValueError if the split is empty, has non-0/1 labels or has duplicate ``transaction_id``."""
    frame = dataset.to_table(columns=INPUT_COLUMNS, filter=window_filter(splits, name)).to_pandas()
    if frame.empty or not frame.is_laundering.isin([0, 1]).all():
        raise ValueError(f'Empty split or invalid labels: {name}')
    if frame.transaction_id.duplicated().any():
        raise ValueError(f'Duplicate transaction IDs: {name}')
    return frame


def encode_columns():
    """The columns ``encode`` reads, plus the label and row index the streamed passes need (looked up at call time, as ``FEATURES`` can be patched)."""
    return ['source_row_index', 'timestamp', 'from_bank', 'from_account', 'to_bank', 'to_account',
            'amount_usd_asof', 'is_laundering', *FEATURES, *CATEGORICAL]


def scan_split(dataset, name, splits, batch_rows=BATCH_ROWS):
    """Streams one split's labels, row indexes and categorical columns, so training can fit its vocabulary and class counts without loading the split.
    Returns ``rows``, ``positives`` and ``vocabulary``; raises the ``read_split`` ValueErrors for an empty split, non-0/1 labels or a repeated ``source_row_index``
    (one-to-one with ``transaction_id``)."""
    rows = positives = 0
    seen = {column: set() for column in CATEGORICAL}
    indexes = []
    for batch in dataset.to_batches(columns=['source_row_index', 'is_laundering', *CATEGORICAL],
                                    filter=window_filter(splits, name), batch_size=batch_rows):
        labels = batch.column('is_laundering').to_numpy(zero_copy_only=False)
        if not np.isin(labels, [0, 1]).all():
            raise ValueError(f'Empty split or invalid labels: {name}')
        rows += len(labels)
        positives += int(labels.sum())
        indexes.append(batch.column('source_row_index').to_numpy(zero_copy_only=False))
        for column in CATEGORICAL:
            seen[column].update(pc.unique(batch.column(column)).to_pylist())
    if not rows:
        raise ValueError(f'Empty split or invalid labels: {name}')
    indexes = np.concatenate(indexes)
    indexes.sort()
    if (indexes[1:] == indexes[:-1]).any():
        raise ValueError(f'Duplicate transaction IDs: {name}')
    # fit_encoder drops nulls and sorts, exactly as for a loaded split.
    vocabulary = fit_encoder(pd.DataFrame({column: pd.Series(list(values)) for column, values in seen.items()}))
    return dict(rows=rows, positives=positives, vocabulary=vocabulary)


def batch_iterator(dataset, name, splits, vocabulary, batch_rows=BATCH_ROWS):
    """An XGBoost ``DataIter`` over one split: each batch is encoded with the fixed ``vocabulary`` and handed over with its labels, so the full matrix never exists.
    ``feature_names`` is set from the first batch. XGBoost reads the stream twice (sketch, then build); a different batch sequence on the second pass raises ValueError."""
    import xgboost as xgb

    class Batches(xgb.DataIter):
        def __init__(self):
            super().__init__()
            self.batches, self.current, self.passes, self.feature_names = None, [], [], None

        def reset(self):
            self.batches, self.current = None, []

        def next(self, input_data):
            if self.batches is None:
                self.batches = dataset.to_batches(columns=encode_columns(), filter=window_filter(splits, name), batch_size=batch_rows)
            batch = next(self.batches, None)
            while batch is not None and not batch.num_rows:
                batch = next(self.batches, None)
            if batch is None:
                if self.current:
                    self.passes.append(self.current)
                    self.current = []
                if any(done != self.passes[0] for done in self.passes[1:]):
                    raise ValueError('Batch order changed between passes')
                return False
            frame = batch.to_pandas()
            matrix = encode(frame, vocabulary)
            self.feature_names = self.feature_names or matrix.columns.tolist()
            index = frame.source_row_index.to_numpy()
            self.current.append((int(index[0]), int(index[-1]), len(frame)))
            input_data(data=matrix, label=frame.is_laundering.to_numpy().astype(np.float32))
            return True

    return Batches()


def score_batches(models, dataset, name, splits, vocabulary, threads, batch_rows=BATCH_ROWS):
    """Streams one split once and scores it with every model. Returns the ranking columns (timestamp, source_row_index, is_laundering, baseline risk_score) and one score array per model."""
    import xgboost as xgb
    columns = list(dict.fromkeys([*encode_columns(), 'risk_score']))
    parts, scores = [], [[] for _ in models]
    for batch in dataset.to_batches(columns=columns, filter=window_filter(splits, name), batch_size=batch_rows):
        if not batch.num_rows:
            continue
        frame = batch.to_pandas()
        matrix = xgb.DMatrix(encode(frame, vocabulary), nthread=threads)
        for out, model in zip(scores, models):
            out.append(model.predict(matrix))
        parts.append(frame[['timestamp', 'source_row_index', 'is_laundering', 'risk_score']])
    return pd.concat(parts, ignore_index=True), [np.concatenate(out) for out in scores]


def daily_timestamp_counts(dataset, batch_rows=BATCH_ROWS):
    """Rows per ISO date, counted from the ``timestamp`` column alone in batches."""
    counts = Counter()
    for batch in dataset.to_batches(columns=['timestamp'], batch_size=batch_rows):
        counts.update(batch.column('timestamp').to_pandas().dt.strftime('%Y-%m-%d').value_counts().to_dict())
    return dict(counts)


def tail_breakdown(frame, predictions, cutoff):
    """Model and baseline metrics for the dense part (before ``cutoff``) and the sparse tail of a window; empty when the window lies wholly on one side."""
    late = (frame.timestamp >= pd.Timestamp(cutoff)).to_numpy()
    if late.all() or not late.any():
        return {}
    result = {}
    for part, mask in [('dense', ~late), ('tail', late)]:
        rows = frame[mask]
        result[part] = dict(model=ranking_metrics(rank(rows, predictions[mask])),
                            baseline=ranking_metrics(rank(rows, rows.risk_score.to_numpy())))
    return result


def explain_queue(model, frame, scores, vocabulary, k=2000):
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

    def queue(self, frame, scores, k=2000):
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
                        'Prior-day amount sent (log)', 'Prior-week amount sent (log)',
                        'Prior-hour incoming count (log)', 'Prior-day incoming count (log)',
                        'Prior-week incoming count (log)', 'Prior-day distinct senders (log)',
                        'Prior-week distinct senders (log)',
                        'Prior-day amount received (log)', 'Prior-week amount received (log)',
                        'Prior transactions with this counterparty (log)'])})
        # Split each "name: +x margin" part at the first ":" and swap the name for its alias.
        queue['model_evidence'] = queue.model_evidence.map(lambda evidence: '; '.join(
            aliases.get(part.split(':', 1)[0], part.split(':', 1)[0]) + ':' + part.split(':', 1)[1]
            for part in evidence.split('; ')))
        return queue


def _average_precision_metric(predt, dtrain):
    """Early-stopping metric: the project's own exact-tie average precision (``analytics.ranking_metrics``),
    not XGBoost's built-in trapezoidal aucpr, so stopping matches what ``choose_candidate`` selects on.
    ``predt`` is already a probability here since ``binary:logistic`` is a builtin objective; no pre-sorted
    order is needed because ``ranking_metrics`` groups by score value itself."""
    frame = pd.DataFrame({'risk_score': predt, 'is_laundering': dtrain.get_label().astype(int)})
    return 'ap', ranking_metrics(frame)['average_precision'] or 0.0


def compare_models(scored, snapshot_path, output, rounds=300, threads=4, batch_rows=BATCH_ROWS, early_stopping='aucpr'):
    """Trains the candidates, picks one on validation, freezes ``selection.json`` and ``features.json`` before reading test data, then evaluates every period against the baseline.
    Training and validation are streamed in batches of ``batch_rows``, so neither split is ever held in memory. Windows that reach into the sparse tail (see ``sparse_tail_start``)
    also report dense-day and tail metrics. ``early_stopping`` picks the validation metric that stops each candidate: ``'aucpr'`` (XGBoost's own, the default) or ``'ap'`` (the exact-tie
    average precision candidates are selected on, which is too slow for very large validation sets: see ``_average_precision_metric``). Writes to a fresh ``output`` directory,
    returns the ``comparison.json`` report and raises ValueError on any input or coverage failure."""
    import xgboost as xgb
    if rounds <= 0 or threads <= 0 or batch_rows <= 0:
        raise ValueError('Rounds, threads and batch rows must be positive')
    if early_stopping not in ('aucpr', 'ap'):
        raise ValueError("early_stopping must be 'aucpr' or 'ap'")
    output = Path(output)
    if output.exists():
        raise ValueError('Use a fresh output directory')
    snapshot = json.loads(Path(snapshot_path).read_text(encoding='utf-8'))
    # 'date-subset' is a deliberately chosen selection of dates out of an otherwise fully-curated
    # run (see batch.export_verified_batch); training candidates may use it, but release.py/
    # model_dashboard.py/dashboard_exploration.py still require 'full' and reject it, so a
    # date-subset candidate can never be published or promoted.
    if snapshot.get('aml_scope') not in ('full', 'date-subset'):
        raise ValueError('Full-population or date-subset snapshot required')
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
    # Pass 1 streams labels and categories only; the vocabulary is fitted on training alone.
    train_scan, valid_scan = scan_split(dataset, 'train', splits, batch_rows), scan_split(dataset, 'validation', splits, batch_rows)
    vocabulary = train_scan['vocabulary']
    positives = train_scan['positives']
    negatives = train_scan['rows'] - positives
    # Training needs both classes; validation needs positives or AP is undefined.
    if not positives or not negatives or not valid_scan['positives']:
        raise ValueError('Training needs both classes and validation needs positives')
    train_counts = dict(rows=train_scan['rows'], positives=positives)
    # QuantileDMatrix bins features batch by batch (sketch pass, then build pass), so the float matrix never exists;
    # ref=dtrain reuses the training bin edges for validation.
    train_batches = batch_iterator(dataset, 'train', splits, vocabulary, batch_rows)
    dtrain = xgb.QuantileDMatrix(train_batches, nthread=threads)
    feature_names = train_batches.feature_names
    dvalid = xgb.QuantileDMatrix(batch_iterator(dataset, 'validation', splits, vocabulary, batch_rows), ref=dtrain, nthread=threads)
    # A scale_pos_weight above 1 up-weights laundering rows, so scores are then not calibrated probabilities.
    # The weight differs per candidate, so it is recorded with each candidate rather than in these shared parameters.
    # Early stopping watches XGBoost's trapezoidal aucpr by default. 'ap' instead uses the custom _average_precision_metric (the exact-tie AP that
    # candidates are selected on), but that runs a pandas group-by over the whole validation set every round: about 0.4 s per million rows and growing
    # faster than linearly, i.e. hours at HI-Large's ~35 million validation rows, and on HI-Small it gave no held-out gain (test AP -0.002).
    params = dict(objective='binary:logistic', tree_method='hist', device='cpu',
                  eta=.1, min_child_weight=10, subsample=1,
                  colsample_bytree=1, seed=459, nthread=threads, **({'eval_metric': 'aucpr'} if early_stopping == 'aucpr' else {}))
    trained = []
    for candidate in CANDIDATES:
        weight = positive_weight(candidate, positives, negatives)
        print(f"Training {candidate['name']} (scale_pos_weight {weight:.4g}) on {train_counts['rows']:,} rows", flush=True)
        candidate_params = {**params, 'max_depth': candidate['max_depth'], 'scale_pos_weight': weight,
                            **{key: candidate[key] for key in PARAM_KEYS if key in candidate}}
        # Stop if the validation metric has not improved for 20 rounds; log every 20.
        model = xgb.train(candidate_params, dtrain,
                          num_boost_round=rounds, evals=[(dvalid, 'validation')],
                          **({'custom_metric': _average_precision_metric, 'maximize': True} if early_stopping == 'ap' else {}),
                          early_stopping_rounds=20, verbose_eval=20)
        # Slice explicitly: later predictions and saved artifacts use selected trees only.
        model = model[:model.best_iteration + 1]
        path = output / (candidate['name'] + '.ubj')
        model.save_model(path)
        trained.append((candidate, weight, model, path))
    # One validation stream scores every candidate.
    validation, predictions = score_batches([model for _, _, model, _ in trained], dataset, 'validation', splits, vocabulary, threads, batch_rows)
    candidates = []
    for (candidate, weight, model, path), scores in zip(trained, predictions):
        candidates.append(dict(**candidate, scale_pos_weight=weight, trees=model.num_boosted_rounds(),
                               metrics=ranking_metrics(rank(validation, scores)), model_file=path.name, sha256=digest(path)))
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
    del dtrain, dvalid, validation
    gc.collect()
    # Days holding under SPARSE_FRACTION of the median day, at the end of the data, are reported apart from the dense days.
    counts = daily_timestamp_counts(dataset, batch_rows)
    # A date-subset selection can carry an explicit tail boundary (set when the dates were chosen),
    # since sparse_tail_start's median-based heuristic is skewed when sparse days outnumber dense
    # ones - exactly the case for a subset deliberately built to include a disproportionate tail.
    explicit_tail_start = snapshot.get('provenance', {}).get('tail_start')
    tail_start = explicit_tail_start or sparse_tail_start(counts)
    sparse_tail = None if tail_start is None else dict(
        fraction=SPARSE_FRACTION, start=tail_start, median_daily_rows=float(np.median(list(counts.values()))))
    metrics = {}
    # Running totals for the coverage check below (start with training).
    total_rows = train_counts['rows']
    total_positives = positives
    for name in eval_names(splits):
        frame = read_split(dataset, name, splits)
        predictions = model.predict(xgb.DMatrix(encode(frame, vocabulary), nthread=threads))
        metrics[name] = dict(model=ranking_metrics(rank(frame, predictions)),
                            baseline=ranking_metrics(rank(frame, frame.risk_score.to_numpy())),
                            **(tail_breakdown(frame, predictions, tail_start) if tail_start else {}))
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
                  total_rows=total_rows, total_positives=total_positives, sparse_tail=sparse_tail,
                  parameters=params, max_rounds=rounds, batch_rows=batch_rows, early_stopping=early_stopping, xgboost_version=xgb.__version__,
                  feature_names=feature_names, selection_sha256=digest(output / 'selection.json'),
                  feature_schema_sha256=digest(output / 'features.json'),
                  limitations=['Logistic ranking scores (weighted or not) are not calibrated probabilities.',
                               'Exploratory synthetic dataset; prior whole-data EDA limits holdout claims.',
                               'Cold-start account history; timestamps have no verified timezone.',
                               'TreeSHAP explains model margins, not causal laundering evidence.',
                               'No model promotion, AWS training, or dashboard replacement.'])
    # allow_nan=False: fail rather than write invalid JSON if a metric is NaN.
    (output / 'comparison.json').write_text(json.dumps(report, indent=2, allow_nan=False), encoding='utf-8')
    return report
