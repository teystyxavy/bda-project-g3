"""Shared Kaggle file selection for the local downloader and ingestion launcher."""

SUBSETS = tuple(f'{variant}-{size}' for variant in ('HI', 'LI') for size in ('Small', 'Medium', 'Large'))
FILE_SUFFIXES = {'transactions': 'Trans.csv', 'accounts': 'accounts.csv', 'patterns': 'Patterns.txt'}


def resolve_subset(subset=None, variant=None, size=None):
    variant = variant.upper() if variant else None
    size = size.title() if size else None
    if subset:
        parts = subset.split('-')
        if len(parts) != 2:
            raise ValueError('Subset must be HI-Small, HI-Medium, HI-Large, LI-Small, LI-Medium or LI-Large')
        selected_variant, selected_size = parts[0].upper(), parts[1].title()
        if ((variant and variant != selected_variant) or (size and size != selected_size)):
            raise ValueError('--subset conflicts with --variant or --size')
        variant, size = selected_variant, selected_size
    result = f'{variant or "HI"}-{size or "Small"}'
    if result not in SUBSETS:
        raise ValueError('Unsupported Kaggle subset')
    return result


def selected_files(subset, files=None, include_patterns=False):
    subset = resolve_subset(subset)
    kinds = list(dict.fromkeys(files if files is not None else ['transactions', 'accounts']))
    if include_patterns and 'patterns' not in kinds:
        kinds.append('patterns')
    if not kinds or any(kind not in FILE_SUFFIXES for kind in kinds):
        raise ValueError('Files must select transactions, accounts or patterns')
    return [f'{subset}_{FILE_SUFFIXES[kind]}' for kind in kinds]


def add_selection_arguments(parser):
    parser.add_argument('--subset', help='Combined variant/size, e.g. LI-Medium (default: HI-Small)')
    parser.add_argument('--variant', '--prefix', type=str.upper, choices=['HI', 'LI'],
                        help='High (HI) or low (LI) illicit-ratio variant; default HI')
    parser.add_argument('--size', type=str.title, choices=['Small', 'Medium', 'Large'], help='Default Small')
    parser.add_argument('--files', nargs='+', type=str.lower, choices=list(FILE_SUFFIXES),
                        help='Download-only file selection; default transactions accounts')
    parser.add_argument('--include-patterns', action='store_true', help='Also download pattern descriptions')
    parser.add_argument('--dry-run', action='store_true', help='Preview selection without network calls or file writes')
    parser.add_argument('--list-options', action='store_true', help='List dataset/file choices without downloading')


def selection_options():
    return {'subsets': list(SUBSETS), 'variants': ['HI', 'LI'], 'sizes': ['Small', 'Medium', 'Large'],
            'files': list(FILE_SUFFIXES), 'default_subset': 'HI-Small',
            'default_files': ['transactions', 'accounts'], 'pipeline_subsets': ['HI-Small']}
