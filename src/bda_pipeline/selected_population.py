"""Explicit completeness contract for a selected evaluation population; no AWS calls."""
from datetime import date, timedelta
import re


def selection_days(selection):
    start, end = date.fromisoformat(selection['start_date']), date.fromisoformat(selection['end_date'])
    if start > end:
        raise ValueError('Selection date range is reversed')
    return [(start + timedelta(days=i)).isoformat() for i in range((end - start).days + 1)]


def validate_selection(manifest):
    selection = manifest.get('selection')
    if not isinstance(selection, dict) or selection.get('kind') != 'date-range':
        raise ValueError('Explicit date-range selection required')
    days = selection_days(selection)
    if (manifest.get('train') is not False or manifest.get('export_dates') is not None
            or manifest.get('source_scope') != 'date-subset'
            or (manifest['start_date'], manifest['end_date']) != (days[0], days[-1])
            or type(manifest.get('source_index_rows')) is not int
            or not 0 < manifest['rows'] <= manifest['source_index_rows']):
        raise ValueError('Inconsistent selected population')
    if selection.get('network_scope') != 'full-source' or manifest.get('network_rows') != manifest['source_index_rows']:
        raise ValueError('Full-source network scope required')
    for key in ['source_manifest_sha256', 'source_proof_sha256', 'training_snapshot_sha256']:
        if not re.fullmatch('[0-9a-f]{64}', selection.get(key, '')):
            raise ValueError('Selection provenance hash required')
    return days


def validate_snapshot_population(snapshot):
    population = snapshot.get('population', {})
    proof = snapshot.get('provenance', {}).get('subset_verification', {})
    if (population.get('kind') != 'date-range' or proof.get('status') != 'passed'
            or population.get('rows') != snapshot.get('full_rows')
            or population.get('rows') != snapshot.get('cohort_rows')
            or proof.get('rows') != population.get('rows')
            or snapshot.get('source_index_rows', 0) < population.get('rows', 0)
            or not population.get('source_run_id')
            or len(selection_days(population)) != proof.get('days')):
        raise ValueError('Unverified or incomplete selected population')
    for key in ['source_manifest_sha256', 'source_proof_sha256', 'training_snapshot_sha256']:
        if not re.fullmatch('[0-9a-f]{64}', population.get(key, '')):
            raise ValueError('Selected population provenance required')
    return population
