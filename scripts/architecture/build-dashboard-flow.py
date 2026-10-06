"""Create editable AWS diagrams for dashboard ingestion. No AWS calls."""
import importlib.util
import xml.etree.ElementTree as ET
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location('architecture_shapes', Path(__file__).with_name('build-architecture-diagrams.py'))
SHAPES = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SHAPES)
# SageMaker is verified in the trusted AWS4 stencil reference, as documented in
# planning/architecture/automated-ingestion.md. Other icons are in the local skill.
SHAPES.COLORS.update({'sagemaker': '#01A88D', 'api_gateway': '#E7157B'})
SHAPES.VERIFIED.update({'sagemaker', 'api_gateway'})


def page(file, name, title, subtitle):
    result = SHAPES.Page(file, name, title, subtitle)
    result.root.find("mxCell[@id='title']").set('value',
        '<b>' + title + '</b><br>Group 3 | 6 October 2026 | UI implementation; deployment pending')
    return result


def build():
    file = ET.Element('mxfile', host='app.diagrams.net', version='31.7.0')
    p = page(file, 'Ingestion and analysis', 'Dashboard-controlled batch pipeline: ingestion and parallel analysis',
             'The dashboard API/sign-in are implemented locally and await deployment. The diagram describes the existing batch workflows, not a new AWS acceptance run.')
    p.group('control', 'Protected run controls', 40, 220, 1500, 310)
    p.local('ui', 'Dashboard Ingestion tab', 'HI-Small demo/full selection<br>operator sign-in', 55, 115, 'control')
    p.icon('api', 'API Gateway', 'JWT access token<br>operator-only routes', 410, 120, 'api_gateway', 'control')
    p.icon('api_lambda', 'Lambda control API', 'Stable submission ID<br>DynamoDB launch reservation', 750, 120, 'lambda', 'control')
    p.icon('states', 'Step Functions', 'Acquisition wrapper<br>existing batch state machine', 1090, 120, 'step_functions', 'control')
    for i, (source, target) in enumerate([('ui', 'api'), ('api', 'api_lambda'), ('api_lambda', 'states')]):
        p.edge('control_edge_' + str(i), source, target)
    p.note('sources', '<b>External sources</b><br>Kaggle IBM AML HI-Small<br>historical daily FX', 1800, 245, 470, 135, 19)
    p.note('orchestration', 'Step Functions dispatches the jobs below and waits for verified completion. The arrows represent data/dependency flow, not network connections or a claim that all jobs run in order.', 70, 560, 2220, 70, 17)
    p.icon('acquire', 'Lambda acquisition', 'Kaggle + historical FX<br>validated source inputs', 100, 750, 'lambda', side_label=True)
    p.icon('raw', 'S3 raw', 'Encrypted transaction CSV<br>account metadata + FX', 100, 1050, 's3')
    p.icon('curate', 'Glue curation', 'Causal behaviour features<br>USD conversion + account joins', 440, 750, 'glue', side_label=True)
    p.icon('curated', 'S3 curated', 'Date-partitioned Parquet<br>shared by both business views', 780, 750, 's3', side_label=True)
    p.icon('score', 'Glue scoring', 'BQ1 frozen-model queue<br>S3 scored Parquet', 1120, 750, 'glue', side_label=True)
    p.icon('athena', 'Athena', 'BQ2 corridor SQL<br>S3 via Glue catalog', 1120, 940, 'athena')
    p.icon('baseline', 'SageMaker Processing', 'Rule baseline verification<br>full-run candidate preparation', 1460, 1190, 'sagemaker', side_label=True)
    p.icon('train', 'SageMaker Training', 'Optional private candidate<br>no automatic promotion', 1800, 1190, 'sagemaker', side_label=True)
    p.icon('export', 'SageMaker Processing', 'Reconcile and build results<br>then publication (page 2)', 2080, 750, 'sagemaker')
    export_cell = p.root.find("mxCell[@id='export']")
    export_cell.set('style', export_cell.get('style') +
                    'labelPosition=right;verticalLabelPosition=middle;verticalAlign=middle;align=left;labelWidth=180;spacingLeft=15;')
    p.edge('inputs', 'acquire', 'raw', exit=(.5, 1), entry=(.5, 0))
    p.edge('raw_to_curate', 'raw', 'curate', exit=(1, .5), entry=(.5, 1))
    p.edge('curated_output', 'curate', 'curated')
    p.edge('scoring', 'curated', 'score')
    p.edge('treasury', 'curated', 'athena', exit=(.7, 1), entry=(0, .5))
    p.edge('baseline_branch', 'curated', 'baseline', exit=(.3, 1), entry=(0, .5))
    p.edge('score_export', 'score', 'export')
    p.edge('athena_export', 'athena', 'export', exit=(1, .5), entry=(.15, 1))
    p.edge('baseline_export', 'baseline', 'export', diagonal=True, exit=(1, .5), entry=(0, 1))
    p.edge('candidate', 'baseline', 'train', dashed=True)
    p.edge('candidate_export', 'train', 'export', dashed=True, exit=(1, .5), entry=(.9, 1))
    p.note('legend', 'Solid: required data/dependency flow. Dashed: optional candidate training. Baseline verification, scoring and treasury execute in parallel after curation; export waits for all requested branches.', 70, 1300, 2220, 75, 16)

    p = page(file, 'Verification and publication', 'Verified publication and live dashboard service status',
             'Only verified release assets are published. A reused release skips new batch work and keeps the new dashboard shell; no model is promoted by ingestion.')
    p.icon('export', 'SageMaker Processing', 'Score/FX/corridor reconciliation<br>build four release assets', 160, 520, 'sagemaker')
    p.icon('publish', 'Lambda publication', 'Verify hashes and proofs<br>commit current pointer', 560, 520, 'lambda')
    p.icon('site', 'S3 dashboard origin', 'Private encrypted bucket<br>immutable HTML/JS/CSS/JSON', 960, 520, 's3')
    p.icon('cdn', 'CloudFront', 'Serve verified business views<br>invalidate root assets', 1360, 520, 'cloudfront', side_label=True)
    p.local('browser', 'Dashboard UI', 'AML review + treasury views<br>Ingestion service diagram', 110, 880)
    p.icon('states', 'Step Functions', 'Parent + child state history<br>phase/service progress', 1760, 520, 'step_functions')
    for i, (source, target) in enumerate([('export', 'publish'), ('publish', 'site'), ('site', 'cdn')]):
        p.edge('publication_' + str(i), source, target)
    p.edge('results', 'cdn', 'browser', exit=(.5, 1), entry=(1, .5))
    p.note('status', '<b>Live status</b><br>Authenticated API reads both workflow histories without payloads. The browser updates phase colours and names the active Lambda, Glue, Athena or SageMaker step every 10 seconds.', 1710, 770, 580, 180, 19)
    p.note('meaning', '<b>Progress meaning</b><br>Green: active phase. Light green: complete. Purple: reused/skipped. Red: failed/stopped. Parallel boxes may be active together. S3 writes inside acquisition and publication do not have individual byte/row counters.', 65, 1130, 2200, 150, 19)
    for diagram in file.findall('diagram'):
        cells = diagram.findall('./mxGraphModel/root/mxCell')
        ids = [cell.get('id') for cell in cells]
        assert len(ids) == len(set(ids))
        for cell in cells:
            if cell.get('edge') == '1':
                assert cell.get('source') in ids and cell.get('target') in ids
                assert cell.find('mxGeometry').get('relative') == '1'
    ET.indent(file, space='  ')
    ET.ElementTree(file).write(ROOT / 'planning/architecture/dashboard-ingestion-flow.drawio',
                               encoding='utf-8', xml_declaration=True)
    print('Dashboard ingestion diagram: 2 pages; XML and edge references validated')


if __name__ == '__main__':
    build()
