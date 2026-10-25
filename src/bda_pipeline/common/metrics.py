"""Ranking-quality metrics shared by the rule baseline, the model and the dashboards.

``ranking_metrics`` is the single definition of precision@K, recall@K,
lift@K and tie-aware average precision, so the XGBoost model and the rule
baseline are always compared with the same metric. It reads the
``is_laundering`` label, which is only ever used for evaluation, never to
compute a score.
"""


def ranking_metrics(ranked, k=100):
    """Average precision (stepwise PR area), plus deterministic top-K metrics.

    "Top-K" mirrors the business setting: a compliance team can only review
    about K=100 alerts, so what matters is how many true laundering cases land
    at the top of the queue.

    Args:
        ranked: Rows already sorted into queue order, with ``risk_score`` and
            the 0/1 ``is_laundering`` label.
        k: Queue size to evaluate (default 100).

    Returns:
        dict with:
        ``rows`` and ``positives`` (laundering count);
        ``k`` (K, capped at the number of rows);
        ``precision_at_k`` = hits in top K / K;
        ``recall_at_k`` = hits in top K / all positives;
        ``lift_at_k`` = precision@K / overall positive rate (how many times
        better than picking K rows at random);
        ``average_precision`` - area under the precision-recall curve.
        Metrics that would divide by zero are ``None``.

    AP and ties: rows with equal ``risk_score`` cannot be meaningfully ordered
    (the rule score has only a few distinct values), so AP treats each score
    level as one step. For each level, from highest score down:
    ``(positives at this level / total positives) * (cumulative precision
    after including the whole level)``. This makes AP independent of the
    arbitrary order inside a tie.
    """
    n = len(ranked)
    positives = int(ranked.is_laundering.sum())
    effective_k = min(k, n)
    hits = int(ranked.head(k).is_laundering.sum())
    precision = hits/effective_k if effective_k else None
    ap = None
    if positives:
        # One row per distinct score, highest first: sum = positives, count = rows.
        grouped = ranked.groupby('risk_score').is_laundering.agg(['sum', 'count']).sort_index(ascending=False)
        # recall gained at this level * precision at the end of this level.
        ap = float(((grouped['sum']/positives) * (grouped['sum'].cumsum()/grouped['count'].cumsum())).sum())
    return dict(rows=n, positives=positives, k=effective_k, precision_at_k=precision,
                recall_at_k=hits/positives if positives else None,
                lift_at_k=precision/(positives/n) if positives and precision is not None else None,
                average_precision=ap)
