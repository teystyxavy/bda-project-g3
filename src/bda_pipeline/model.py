"""Local, chronological XGBoost comparison; never tune on the test split.

Purpose
-------
BQ1 asks for a *ranked, explainable* AML review queue. The rule-based baseline
(``analytics.score_baseline``) is easy to explain but coarse. This module trains
a gradient-boosted tree model (XGBoost) on the same curated features, compares
it to the baseline with identical metrics, and explains each queued alert with
exact TreeSHAP feature contributions.

Where it sits in the pipeline
-----------------------------
Input is ``bq1_scored.parquet``: the full curated dataset already scored by the
baseline (written by ``batch.export_verified_batch``), plus the dashboard
snapshot ``dashboard.json`` that records that file's SHA-256 hash, row count
and per-period positive counts. Both are checked before training so the model
is provably trained on the same verified data the dashboard shows.

Entry points:

* ``scripts/modeling/compare-local-model.py`` - runs ``compare_models`` on a
  laptop.
* ``sagemaker_training.train_channels`` - runs ``compare_models`` inside the
  SageMaker training container (``sagemaker/train.py``).
* ``SavedRanker`` - reloads the frozen, hash-checked model for inference; used
  by ``inference.py`` (Glue batch scoring and the Kinesis->Lambda streaming
  demo), ``cloud_scores.py``, ``model_dashboard.py`` and ``ingest_compute.py``.

Outputs written to a *fresh* output directory by ``compare_models``:

* ``hist-depth3.ubj`` / ``hist-depth5.ubj`` - the two candidate models
  (XGBoost universal binary JSON format);
* ``selection.json`` - which candidate won and why, written *before* any test
  data is read ("frozen before test");
* ``features.json`` - category vocabulary and feature names (the schema);
* ``model_queue_{validation,test,tail}.csv`` - top-100 explained queues;
* ``comparison.json`` - full report including hashes of the files above.

Chronological splits (``SPLITS``, start inclusive / end exclusive) prevent the
model from learning from the future: train on 1-6 Sep 2022, choose the model
on 7-8 Sep (validation), report on 9-10 Sep (test) and the sparse 11-18 Sep
tail separately.
"""
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

# String columns that are one-hot encoded for the model.
CATEGORICAL = ['payment_format', 'payment_currency', 'receiving_currency']
# Only these columns are read from Parquet (saves memory on the full dataset).
# IDs, timestamp, the label and the baseline score are needed for ranking,
# evaluation and display - they are NOT model features (see ``encode``).
INPUT_COLUMNS = ['transaction_id', 'source_row_index', 'timestamp', 'from_bank',
                 'from_account', 'to_bank', 'to_account', 'amount_usd_asof',
                 'is_laundering', 'risk_score', 'risk_reasons', *FEATURES, *CATEGORICAL]
# Time windows for each split: (start inclusive, end exclusive).
SPLITS = {'train': ('2022-09-01', '2022-09-07'),
          'validation': ('2022-09-07', '2022-09-09'),
          'test': ('2022-09-09', '2022-09-11'),
          'tail': ('2022-09-11', '2022-09-19')}
# The only hyperparameter compared: tree depth (shallow vs. moderately deep).
# Keeping the search tiny and pre-declared limits overfitting to validation.
CANDIDATES = [{'name': 'hist-depth3', 'max_depth': 3},
              {'name': 'hist-depth5', 'max_depth': 5}]


def digest(path):
    """Return the SHA-256 hex digest of a file.

    Used everywhere in the project as a tamper/consistency check: inputs,
    model files and manifests are recorded by hash and re-verified before use.
    """
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def fit_encoder(training):
    """No IDs, labels, absolute dates, same-day valuation or target encoding.

    Builds the category vocabulary from the *training split only*: for each
    column in ``CATEGORICAL`` a sorted list of the values seen in training.
    Categories that first appear later are not given their own column; they
    switch on the ``<column>_unknown`` flag in ``encode`` instead.

    The title line records deliberate exclusions that prevent leakage or
    memorisation: account/bank IDs, the label, calendar dates, the same-day
    ``amount_usd`` (uses a rate that might be published after the payment)
    and target encoding (which would leak label information into features).

    Args:
        training: Training-split DataFrame.

    Returns:
        ``{column: [category, ...]}`` - stored in ``features.json``.
    """
    return {column: sorted(training[column].dropna().unique().tolist())
            for column in CATEGORICAL}


def encode(frame, vocabulary):
    """Turn transactions into the numeric feature matrix the model sees.

    Features produced, in column order:

    * ``log1p_amount_usd_asof`` and ``log1p_<behaviour feature>``: log(1 + x)
      of the prior-day USD amount and the five velocity/fan-out counts. Logs
      compress the huge range of amounts/counts.
    * ``is_self_txn``: 1.0 if sender and receiver are the same zero-stripped
      bank and the same account.
    * ``currency_mismatch``: 1.0 if paid and received currencies differ.
    * ``hour_sin`` / ``hour_cos``: hour of day placed on a circle so that
      23:00 and 00:00 are close together (a plain 0-23 number would put them
      far apart).
    * one-hot ``<column>_<index>`` for each vocabulary category, plus
      ``<column>_unknown`` for unseen categories. Columns are named by index
      (not by value) so names are always safe for XGBoost.

    Args:
        frame: Rows with the columns in ``INPUT_COLUMNS``/``SCORING_COLUMNS``.
        vocabulary: Output of ``fit_encoder``.

    Returns:
        float32 DataFrame with the same index as ``frame``. Column order is
        deterministic, which ``SavedRanker`` relies on to match the model.

    Raises:
        ValueError: on negative/non-finite numeric values or missing
            categories, rather than letting the model score bad input.
    """
    values = {}
    for column in ['amount_usd_asof', *FEATURES]:
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
    # Cyclic encoding: angle = hour / 24 of a full turn (2*pi radians).
    hour = frame.timestamp.dt.hour.to_numpy()
    values['hour_sin'] = np.sin(hour * 2 * np.pi / 24).astype(np.float32)
    values['hour_cos'] = np.cos(hour * 2 * np.pi / 24).astype(np.float32)
    for column in CATEGORICAL:
        if frame[column].isna().any():
            raise ValueError(f'Missing categorical feature: {column}')
        # One 0/1 column per known category.
        for index, category in enumerate(vocabulary[column]):
            values[f'{column}_{index}'] = frame[column].eq(category).to_numpy(dtype=np.float32)
        # Catch-all for categories never seen in training.
        values[column + '_unknown'] = (~frame[column].isin(vocabulary[column])).to_numpy(dtype=np.float32)
    return pd.DataFrame(values, index=frame.index)


def rank(frame, scores):
    """Attach model scores and sort into review-queue order.

    Ordering matches ``analytics.score_baseline``: highest score first, then
    earliest timestamp, then original file order, using a stable sort so the
    queue is reproducible. The scores are written into ``risk_score`` so
    ``analytics.ranking_metrics`` can evaluate model and baseline identically.

    Args:
        frame: Rows to rank (must include ``timestamp`` and
            ``source_row_index``).
        scores: 1-D array of model scores, one per row.

    Returns:
        Sorted copy of ``frame`` with ``risk_score`` replaced by ``scores``.

    Raises:
        ValueError: if the score count differs or any score is NaN/inf.
    """
    if len(scores) != len(frame) or not np.isfinite(scores).all():
        raise ValueError('Invalid prediction scores')
    return frame.assign(risk_score=scores).sort_values(
        ['risk_score', 'timestamp', 'source_row_index'],
        ascending=[False, True, True], kind='stable')


def choose_candidate(candidates):
    """Preregistered selection: validation AP, precision@100, simpler name.

    The rule was fixed before training so the choice cannot be nudged after
    seeing results. Sort key: highest validation average precision, then
    highest precision@100 (``None`` counts as 0), then alphabetical name as a
    deterministic final tie-break ("hist-depth3" < "hist-depth5", so the
    simpler model wins an exact tie).

    Args:
        candidates: List of dicts each holding ``name`` and ``metrics``.

    Returns:
        The winning candidate dict.
    """
    return sorted(candidates, key=lambda c: (-(c['metrics']['average_precision'] or 0),
                  -(c['metrics']['precision_at_k'] or 0), c['name']))[0]


def read_split(dataset, name):
    """Load one chronological split from the scored Parquet dataset.

    The timestamp filter is pushed down to pyarrow, so only the matching rows
    (and only ``INPUT_COLUMNS``) are read into memory - important because the
    full dataset is too large to load at once on the training instance.

    Args:
        dataset: ``pyarrow.dataset.Dataset`` over ``bq1_scored.parquet``.
        name: A key of ``SPLITS``.

    Returns:
        pandas DataFrame for that time window.

    Raises:
        ValueError: if the split is empty, has labels other than 0/1, or has
            duplicate ``transaction_id`` values.
    """
    start, end = SPLITS[name]
    condition = ((ds.field('timestamp') >= pd.Timestamp(start)) &
                 (ds.field('timestamp') < pd.Timestamp(end)))
    frame = dataset.to_table(columns=INPUT_COLUMNS, filter=condition).to_pandas()
    if frame.empty or not frame.is_laundering.isin([0, 1]).all():
        raise ValueError(f'Empty split or invalid labels: {name}')
    if frame.transaction_id.duplicated().any():
        raise ValueError(f'Duplicate transaction IDs: {name}')
    return frame


def explain_queue(model, frame, scores, vocabulary, k=100):
    """Exact TreeSHAP contributions to the model margin, not causal claims.

    Builds the top-``k`` review queue and, for each alert, lists the three
    features that pushed its score up the most. XGBoost's ``pred_contribs``
    computes exact SHAP values for tree models: one contribution per feature
    plus a final "bias" column, and these add up to the model's raw *margin*
    (log-odds). Applying the sigmoid ``1 / (1 + e^-margin)`` to that sum must
    reproduce the predicted score - this is checked as a safety net.

    Args:
        model: Trained ``xgboost.Booster``.
        frame: All rows of the period being queued.
        scores: Model scores for ``frame`` (same order).
        vocabulary: Category vocabulary used to encode the rows.
        k: Queue length (default 100).

    Returns:
        DataFrame of the top ``k`` rows with ``QUEUE_COLUMNS`` where
        ``risk_score`` is renamed ``model_score`` and ``risk_reasons`` is
        renamed ``model_evidence`` (e.g. ``"log1p_txn_count_24h: +1.2345
        margin; ..."``), plus ``explanation_bias_margin`` and
        ``explanation_total_margin``.

    Raises:
        ValueError: if the SHAP contributions do not reproduce the scores.
    """
    import xgboost as xgb
    # Sort only keys and scores; copy feature/string columns for displayed rows.
    # (Sorting the full frame would copy every column of millions of rows.)
    ordering = frame[['timestamp', 'source_row_index']].reset_index(drop=True)
    selected = rank(ordering, scores).head(k)
    # selected.index holds positions into frame (thanks to reset_index above).
    queue = frame.iloc[selected.index].copy()
    queue['risk_score'] = selected.risk_score.to_numpy()
    matrix = encode(queue, vocabulary)
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
    """Reload a frozen model/schema after verifying their recorded hashes.

    Loading checks, in order (any failure raises ``ValueError``):

    1. ``selection.json`` and ``features.json`` match the hashes stored in
       ``comparison.json``;
    2. the selection inside ``comparison.json`` equals ``selection.json`` and
       is marked ``frozen_before_test``;
    3. the selected model filename is a plain ``*.ubj`` name (no path parts,
       so a tampered file cannot point outside the directory) and its hash
       matches;
    4. the model's stored feature names equal ``features.json``.

    This guarantees that whatever scores transactions in Glue, Lambda or the
    dashboard is exactly the model that was evaluated.

    Attributes:
        report, selection: Parsed ``comparison.json`` / ``selection.json``.
        vocabulary, feature_names: The encoding schema.
        model: The loaded ``xgboost.Booster``.
        threads: CPU threads used for prediction.
    """
    def __init__(self, directory, threads=4):
        """Verify and load the artifacts in ``directory`` (see class docstring).

        Args:
            directory: Folder holding ``comparison.json``, ``selection.json``,
                ``features.json`` and the selected ``.ubj`` model.
            threads: XGBoost ``nthread`` for prediction.
        """
        # Imported lazily so modules that only need helpers do not require xgboost.
        import xgboost as xgb
        directory = Path(directory)
        self.report = json.loads((directory / 'comparison.json').read_text(encoding='utf-8'))
        for name, key in [('selection.json', 'selection_sha256'),
                          ('features.json', 'feature_schema_sha256')]:
            if digest(directory / name) != self.report[key]:
                raise ValueError(f'Artifact hash mismatch: {name}')
        self.selection = json.loads((directory / 'selection.json').read_text(encoding='utf-8'))
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
        """Encode ``frame`` and return model scores (0..1, one per row).

        Scores come from a class-weighted logistic objective, so they rank
        well but are not calibrated probabilities.

        Raises:
            ValueError: if the encoded columns differ from the saved schema.
        """
        import xgboost as xgb
        matrix = encode(frame, self.vocabulary)
        if matrix.columns.tolist() != self.feature_names:
            raise ValueError('Encoded feature schema mismatch')
        return self.model.predict(xgb.DMatrix(matrix, nthread=self.threads))

    def queue(self, frame, scores, k=100):
        """Build the explained top-``k`` queue with human-readable evidence.

        Same as ``explain_queue`` but rewrites the technical feature names in
        ``model_evidence`` into labels a compliance reviewer can read, e.g.
        ``payment_format_0`` -> ``payment_format=<first vocabulary value>``
        and ``log1p_txn_count_1h`` -> ``Prior-hour transaction count (log)``.
        The numeric contribution after the ``:`` is kept unchanged.

        Args:
            frame: Rows to queue; scores: their model scores; k: queue size.

        Returns:
            The explained queue DataFrame.
        """
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
        # Each evidence string is "name: +x margin; name: +y margin; ...".
        # Split each part at the first ":" and swap the name for its alias.
        queue['model_evidence'] = queue.model_evidence.map(lambda evidence: '; '.join(
            aliases.get(part.split(':', 1)[0], part.split(':', 1)[0]) + ':' + part.split(':', 1)[1]
            for part in evidence.split('; ')))
        return queue


def compare_models(scored, snapshot_path, output, rounds=120, threads=4):
    """Train candidate XGBoost models, pick one on validation, then evaluate.

    Steps:

    1. Verify inputs: ``scored`` (``bq1_scored.parquet``) must hash-match the
       entry in the dashboard snapshot, the snapshot must cover the full
       population, and row counts must agree.
    2. Load train and validation splits, fit the category vocabulary on
       training, and encode both.
    3. Train each candidate in ``CANDIDATES`` with early stopping on
       validation AUC-PR, then compute validation ranking metrics.
    4. Choose the winner with the pre-registered rule, compare with the rule
       baseline (the "validation gate"), and write ``selection.json`` and
       ``features.json`` BEFORE touching test data.
    5. Score validation, test and tail with the chosen model, record model
       vs. baseline metrics and write explained top-100 queues.
    6. Check every row/positive was covered exactly once and write
       ``comparison.json``.

    Args:
        scored: Path to ``bq1_scored.parquet``.
        snapshot_path: Path to the verified ``dashboard.json`` snapshot.
        output: New directory for all artifacts (must not exist).
        rounds: Maximum boosting rounds per candidate.
        threads: CPU threads for XGBoost.

    Returns:
        The report dict that is also saved as ``comparison.json``.

    Raises:
        ValueError: on any input, coverage or consistency failure.

    Memory: the full dataset is large, so intermediate frames are deleted and
    ``gc.collect()`` is called between phases to keep peak RAM low enough for
    an ml.m5.xlarge training instance.
    """
    import xgboost as xgb
    if rounds <= 0 or threads <= 0:
        raise ValueError('Rounds and threads must be positive')
    output = Path(output)
    # Refuse to mix new artifacts with an older run's files.
    if output.exists():
        raise ValueError('Use a fresh output directory')
    snapshot = json.loads(Path(snapshot_path).read_text(encoding='utf-8'))
    # A sample-only snapshot would make the comparison unrepresentative.
    if snapshot.get('aml_scope') != 'full':
        raise ValueError('Full-population snapshot required')
    expected_hash = next((item['sha256'] for item in snapshot['sources']
                          if item['file'] == 'bq1_scored.parquet'), None)
    actual_hash = digest(scored)
    if actual_hash != expected_hash:
        raise ValueError('Scored input hash differs from verified export')
    dataset = ds.dataset(scored, format='parquet')
    if dataset.count_rows() != snapshot['full_rows']:
        raise ValueError('Scored input row count mismatch')
    output.mkdir(parents=True)
    training, validation = read_split(dataset, 'train'), read_split(dataset, 'validation')
    vocabulary = fit_encoder(training)
    xtrain, xvalid = encode(training, vocabulary), encode(validation, vocabulary)
    positives = int(training.is_laundering.sum())
    negatives = len(training) - positives
    # Training needs both classes; validation needs positives or AP is undefined.
    if not positives or not negatives or not validation.is_laundering.sum():
        raise ValueError('Training needs both classes and validation needs positives')
    train_counts = dict(rows=len(training), positives=positives)
    feature_names = xtrain.columns.tolist()
    # QuantileDMatrix pre-bins features for tree_method='hist', using far less
    # memory than a plain DMatrix. ref=dtrain makes validation reuse the
    # training bin edges so both are bucketed identically.
    dtrain = xgb.QuantileDMatrix(xtrain, label=training.is_laundering, nthread=threads)
    dvalid = xgb.QuantileDMatrix(xvalid, label=validation.is_laundering, ref=dtrain, nthread=threads)
    # Free the raw training frames; only the compact DMatrix is still needed.
    del training, xtrain
    gc.collect()
    # binary:logistic - output is a 0..1 score. aucpr (area under PR curve)
    # suits rare positives better than accuracy or ROC AUC. eta=0.1 learning
    # rate, min_child_weight=10 avoids leaves built from a handful of rows.
    # subsample/colsample=1 and a fixed seed keep training deterministic.
    # scale_pos_weight = negatives/positives up-weights the rare laundering
    # class so the model does not just predict "clean" for everything; the
    # side effect is that scores are not calibrated probabilities.
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
    # The gate is recorded, not enforced: it documents whether the model beat
    # the baseline on validation, but nothing is deployed automatically.
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
    # Load one period at a time to keep memory bounded.
    for name in ['validation', 'test', 'tail']:
        frame = read_split(dataset, name)
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
    # The four splits must cover every row exactly once, and their positives
    # must match those recorded in the verified dashboard snapshot (training
    # positives are added separately because only the validation/test/tail
    # period counts are taken from the snapshot).
    if total_rows != snapshot['full_rows'] or total_positives != sum(
            snapshot['aml'][p]['metrics']['positives'] for p in ['validation', 'test', 'tail']) + positives:
        raise ValueError('Split coverage mismatch')
    # Hashes of selection/features let SavedRanker detect later tampering.
    report = dict(selection=selection, metrics=metrics, train=train_counts,
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
