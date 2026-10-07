"""Shared Kaggle file selection for the local downloader and ingestion launcher.

The Kaggle dataset ``ealtman2019/ibm-transactions-for-anti-money-laundering-aml``
ships six synthetic "subsets": a high-illicit (HI) and low-illicit (LI) variant,
each in Small, Medium and Large sizes. Each subset has up to three files::

    <subset>_Trans.csv      the transactions (what the pipeline ingests)
    <subset>_accounts.csv   account -> bank/entity metadata (joined in curation)
    <subset>_Patterns.txt   descriptions of the injected laundering patterns

This module is the single place that knows those names, so that
``scripts/ingestion/kaggle-download.py`` and ``scripts/ingestion/ingest.py`` (and
``acquisition.acquire_dataset``) all accept the same ``--subset / --variant /
--size / --files`` options and resolve them identically.

It is pure Python (no pandas, no AWS) so it can also be packaged into the
acquisition Lambda image. It reads and writes nothing; it only validates names.

Note: only ``HI-Small`` is supported end to end by the AWS pipeline (the frozen
model and date contract were reviewed on it); the other subsets can only be
downloaded for exploration.
"""

# All six valid subset names, e.g. 'HI-Small', 'LI-Large'. Built from the two
# variants x three sizes so the list cannot drift out of sync with the choices.
SUBSETS = tuple(f'{variant}-{size}' for variant in ('HI', 'LI') for size in ('Small', 'Medium', 'Large'))
# Logical file kind -> the suffix Kaggle uses after '<subset>_'.
FILE_SUFFIXES = {'transactions': 'Trans.csv', 'accounts': 'accounts.csv', 'patterns': 'Patterns.txt'}


def resolve_subset(subset=None, variant=None, size=None):
    """Normalise the user's subset choice into one canonical name like ``'HI-Small'``.

    Users can either pass a combined ``subset`` (``'li-medium'``) or the separate
    ``variant`` (``'LI'``) and ``size`` (``'Medium'``) flags. Case is forgiven.

    Args:
        subset: Optional combined name ``'<VARIANT>-<Size>'``.
        variant: Optional ``'HI'`` or ``'LI'``.
        size: Optional ``'Small'``, ``'Medium'`` or ``'Large'``.

    Returns:
        The canonical subset string. Missing parts default to ``HI`` and
        ``Small``, so calling with no arguments returns ``'HI-Small'``.

    Raises:
        ValueError: if the combined name is malformed, if it contradicts an
        explicitly given variant/size, or if the result is not a known subset.
    """
    # Normalise case first so 'hi' / 'small' are accepted.
    variant = variant.upper() if variant else None
    size = size.title() if size else None
    if subset:
        parts = subset.split('-')
        if len(parts) != 2:
            raise ValueError('Subset must be HI-Small, HI-Medium, HI-Large, LI-Small, LI-Medium or LI-Large')
        selected_variant, selected_size = parts[0].upper(), parts[1].title()
        # Refuse ambiguous input such as --subset HI-Small --size Large rather
        # than silently picking one of them.
        if ((variant and variant != selected_variant) or (size and size != selected_size)):
            raise ValueError('--subset conflicts with --variant or --size')
        variant, size = selected_variant, selected_size
    # Fill in defaults for anything still unspecified.
    result = f'{variant or "HI"}-{size or "Small"}'
    if result not in SUBSETS:
        raise ValueError('Unsupported Kaggle subset')
    return result


def selected_files(subset, files=None, include_patterns=False):
    """Return the exact Kaggle file names to download for a subset.

    Args:
        subset: Subset name (validated/normalised via ``resolve_subset``).
        files: Optional list of file kinds (keys of ``FILE_SUFFIXES``). Defaults
            to ``['transactions', 'accounts']``, the two files the pipeline needs.
        include_patterns: Convenience flag that appends ``'patterns'``.

    Returns:
        A list such as ``['HI-Small_Trans.csv', 'HI-Small_accounts.csv']`` in the
        order requested, with duplicates removed.

    Raises:
        ValueError: if the selection is empty or names an unknown file kind.
    """
    subset = resolve_subset(subset)
    # dict.fromkeys() de-duplicates while keeping the user's order
    # (a set would lose the order).
    kinds = list(dict.fromkeys(files if files is not None else ['transactions', 'accounts']))
    if include_patterns and 'patterns' not in kinds:
        kinds.append('patterns')
    if not kinds or any(kind not in FILE_SUFFIXES for kind in kinds):
        raise ValueError('Files must select transactions, accounts or patterns')
    return [f'{subset}_{FILE_SUFFIXES[kind]}' for kind in kinds]


def add_selection_arguments(parser):
    """Register the shared dataset-selection flags on an ``argparse`` parser.

    Used by both ``kaggle-download.py`` and each sub-command of ``ingest.py`` so
    the CLI options are identical everywhere. The ``type=`` converters normalise
    case at parse time (``--variant hi`` becomes ``'HI'``), and ``choices``
    makes argparse reject typos before any download starts.

    Args:
        parser: An ``argparse.ArgumentParser`` (or sub-parser) to extend in place.

    Returns:
        None. The parser is modified as a side effect.
    """
    parser.add_argument('--subset', help='Combined variant/size, e.g. LI-Medium (default: HI-Small)')
    # '--prefix' is kept as an alias for older instructions/scripts.
    parser.add_argument('--variant', '--prefix', type=str.upper, choices=['HI', 'LI'],
                        help='High (HI) or low (LI) illicit-ratio variant; default HI')
    parser.add_argument('--size', type=str.title, choices=['Small', 'Medium', 'Large'], help='Default Small')
    parser.add_argument('--files', nargs='+', type=str.lower, choices=list(FILE_SUFFIXES),
                        help='Download-only file selection; default transactions accounts')
    parser.add_argument('--include-patterns', action='store_true', help='Also download pattern descriptions')
    # Safety flags: let a user preview what would happen without any side effects.
    parser.add_argument('--dry-run', action='store_true', help='Preview selection without network calls or file writes')
    parser.add_argument('--list-options', action='store_true', help='List dataset/file choices without downloading')


def selection_options():
    """Describe every valid selection, for the ``--list-options`` flag.

    Returns:
        A JSON-serialisable dict listing all subsets, variants, sizes and file
        kinds, the defaults, and ``pipeline_subsets`` - the subsets the AWS
        pipeline actually accepts (currently only ``HI-Small``).
    """
    return {'subsets': list(SUBSETS), 'variants': ['HI', 'LI'], 'sizes': ['Small', 'Medium', 'Large'],
            'files': list(FILE_SUFFIXES), 'default_subset': 'HI-Small',
            'default_files': ['transactions', 'accounts'], 'pipeline_subsets': ['HI-Small']}
