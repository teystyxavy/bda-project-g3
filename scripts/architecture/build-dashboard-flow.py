"""Create editable AWS diagrams for dashboard ingestion. No AWS calls.

Purpose
-------
Generates ``planning/architecture/dashboard-ingestion-flow.drawio``, a
two-page draw.io diagram of the *dashboard-controlled* version of the batch
pipeline (an operator presses "run" in the dashboard's Ingestion tab instead
of running scripts by hand):

* Page 1 "Ingestion and analysis": dashboard UI -> API Gateway (JWT) ->
  Lambda control API -> Step Functions, which then dispatches Lambda
  acquisition (Kaggle + historical FX) -> S3 raw -> Glue curation -> S3
  curated Parquet, followed by parallel BQ1 Glue scoring, BQ2 Athena corridor
  SQL and SageMaker Processing baseline verification (plus optional
  SageMaker Training), all feeding a SageMaker Processing export step.
* Page 2 "Verification and publication": export -> Lambda publication
  (hash/proof checks, current pointer) -> private S3 dashboard origin ->
  CloudFront -> browser, plus how the live status panel reads Step
  Functions history.

How it works
------------
It re-uses the drawing helpers (``Page``, ``icon``, ``edge`` ...) from the
sibling ``build-architecture-diagrams.py``. That file name contains hyphens,
so it cannot be imported with a normal ``import`` statement; instead it is
loaded with ``importlib`` (see SPEC/SHAPES below). Importing it does not
regenerate the other diagrams because those calls are guarded by
``if __name__ == '__main__'``.

How to run
----------
``python scripts/architecture/build-dashboard-flow.py`` (no arguments).
Export to PNG separately with draw.io Desktop if needed. Nothing here
contacts AWS; it only writes the one ``.drawio`` file.
"""
import importlib.util
import xml.etree.ElementTree as ET
from pathlib import Path


# Repository root (two folders above scripts/architecture/).
ROOT = Path(__file__).resolve().parents[2]
# Load build-architecture-diagrams.py as a module named 'architecture_shapes'.
# spec_from_file_location + exec_module is the standard way to import a file
# whose name is not a valid Python identifier (it contains hyphens).
SPEC = importlib.util.spec_from_file_location('architecture_shapes', Path(__file__).with_name('build-architecture-diagrams.py'))
SHAPES = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SHAPES)
# SageMaker is verified in the trusted AWS4 stencil reference, as documented in
# planning/architecture/automated-ingestion.md. Other icons are in the local skill.
# Register two extra service stencils that only this diagram needs. Mutating
# the shared dicts/sets means Page.icon() will accept 'sagemaker' and
# 'api_gateway' (its assert checks SHAPES.VERIFIED).
SHAPES.COLORS.update({'sagemaker': '#01A88D', 'api_gateway': '#E7157B'})
SHAPES.VERIFIED.update({'sagemaker', 'api_gateway'})


def page(file, name, title, subtitle):
    """Create a page using the shared helper, then replace its title banner.

    The shared ``Page`` constructor stamps "Verified implementation" under
    the title. This diagram describes UI work whose deployment is still
    pending, so the title cell is located by id and its label rewritten to
    say "UI implementation; deployment pending" - an honest status label.

    Args:
        file: the ``<mxfile>`` element to add the page to.
        name: page/tab name.
        title: bold heading text.
        subtitle: explanatory line under the title.

    Returns:
        The ``SHAPES.Page`` instance, ready for icons and edges.
    """
    result = SHAPES.Page(file, name, title, subtitle)
    # XPath-style lookup of the <mxCell id="title"> created by Page.__init__.
    result.root.find("mxCell[@id='title']").set('value',
        '<b>' + title + '</b><br>Group 3 | 6 October 2026 | UI implementation; deployment pending')
    return result


def build():
    """Build both pages of the dashboard ingestion diagram and write the file.

    Layout is hand-placed on the 2400 x 1400 canvas. Solid arrows are
    required data/dependency flow; dashed arrows are the optional
    candidate-training path. After building, the same structural checks as
    ``write()`` in the shared module are repeated (unique ids, edges point at
    existing cells, relative edge geometry) before writing the XML.

    Side effects:
        Writes ``planning/architecture/dashboard-ingestion-flow.drawio`` and
        prints a confirmation line.
    """
    # Newer draw.io version string than the shared module (the file was
    # created/checked with draw.io 31.7.0, the same build the PNG exporter uses).
    file = ET.Element('mxfile', host='app.diagrams.net', version='31.7.0')
    # ---------------- Page 1: protected run controls and batch analysis ----------------
    p = page(file, 'Ingestion and analysis', 'Dashboard-controlled batch pipeline: ingestion and parallel analysis',
             'The dashboard API/sign-in are implemented locally and await deployment. The diagram describes the existing batch workflows, not a new AWS acceptance run.')
    # Control lane: who can start a run and how the request reaches Step Functions.
    # API Gateway checks a JWT (operator sign-in); the Lambda control API gives
    # each submission a stable ID and reserves the launch in DynamoDB so a
    # double-click cannot start two pipelines.
    p.group('control', 'Protected run controls', 40, 220, 1500, 310)
    p.local('ui', 'Dashboard Ingestion tab', 'HI-Small demo/full selection<br>operator sign-in', 55, 115, 'control')
    p.icon('api', 'API Gateway', 'JWT access token<br>operator-only routes', 410, 120, 'api_gateway', 'control')
    p.icon('api_lambda', 'Lambda control API', 'Stable submission ID<br>DynamoDB launch reservation', 750, 120, 'lambda', 'control')
    p.icon('states', 'Step Functions', 'Acquisition wrapper<br>existing batch state machine', 1090, 120, 'step_functions', 'control')
    # Chain ui -> api -> api_lambda -> states.
    for i, (source, target) in enumerate([('ui', 'api'), ('api', 'api_lambda'), ('api_lambda', 'states')]):
        p.edge('control_edge_' + str(i), source, target)
    p.note('sources', '<b>External sources</b><br>Kaggle IBM AML HI-Small<br>historical daily FX', 1800, 245, 470, 135, 19)
    p.note('orchestration', 'Step Functions dispatches the jobs below and waits for verified completion. The arrows represent data/dependency flow, not network connections or a claim that all jobs run in order.', 70, 560, 2220, 70, 17)
    # Data/compute stages dispatched by Step Functions (absolute positions, no lane).
    p.icon('acquire', 'Lambda acquisition', 'Kaggle + historical FX<br>validated source inputs', 100, 750, 'lambda', side_label=True)
    p.icon('raw', 'S3 raw', 'Encrypted transaction CSV<br>account metadata + FX', 100, 1050, 's3')
    p.icon('curate', 'Glue curation', 'Causal behaviour features<br>USD conversion + account joins', 440, 750, 'glue', side_label=True)
    p.icon('curated', 'S3 curated', 'Date-partitioned Parquet<br>shared by both business views', 780, 750, 's3', side_label=True)
    p.icon('score', 'Glue scoring', 'BQ1 frozen-model queue<br>S3 scored Parquet', 1120, 750, 'glue', side_label=True)
    p.icon('athena', 'Athena', 'BQ2 corridor SQL<br>S3 via Glue catalog', 1120, 940, 'athena')
    p.icon('baseline', 'SageMaker Processing', 'Rule baseline verification<br>full-run candidate preparation', 1460, 1190, 'sagemaker', side_label=True)
    p.icon('train', 'SageMaker Training', 'Optional private candidate<br>no automatic promotion', 1800, 1190, 'sagemaker', side_label=True)
    p.icon('export', 'SageMaker Processing', 'Reconcile and build results<br>then publication (page 2)', 2080, 750, 'sagemaker')
    # The export icon sits at the right edge, so move its label to the right
    # of the icon (vertically centred) instead of underneath it.
    export_cell = p.root.find("mxCell[@id='export']")
    export_cell.set('style', export_cell.get('style') +
                    'labelPosition=right;verticalLabelPosition=middle;verticalAlign=middle;align=left;labelWidth=180;spacingLeft=15;')
    # Dependency arrows. exit/entry fractions pick which side of each icon the
    # arrow attaches to so lines do not cross labels.
    p.edge('inputs', 'acquire', 'raw', exit=(.5, 1), entry=(.5, 0))
    p.edge('raw_to_curate', 'raw', 'curate', exit=(1, .5), entry=(.5, 1))
    p.edge('curated_output', 'curate', 'curated')
    p.edge('scoring', 'curated', 'score')
    # After curation three branches fan out in parallel: BQ1 scoring, BQ2
    # Athena treasury query and SageMaker baseline verification.
    p.edge('treasury', 'curated', 'athena', exit=(.7, 1), entry=(0, .5))
    p.edge('baseline_branch', 'curated', 'baseline', exit=(.3, 1), entry=(0, .5))
    # Every branch feeds the export step, which waits for all requested branches.
    p.edge('score_export', 'score', 'export')
    p.edge('athena_export', 'athena', 'export', exit=(1, .5), entry=(.15, 1))
    p.edge('baseline_export', 'baseline', 'export', diagonal=True, exit=(1, .5), entry=(0, 1))
    # Optional path (dashed): candidate model training is never auto-promoted.
    p.edge('candidate', 'baseline', 'train', dashed=True)
    p.edge('candidate_export', 'train', 'export', dashed=True, exit=(1, .5), entry=(.9, 1))
    p.note('legend', 'Solid: required data/dependency flow. Dashed: optional candidate training. Baseline verification, scoring and treasury execute in parallel after curation; export waits for all requested branches.', 70, 1300, 2220, 75, 16)

    # ---------------- Page 2: verification and publication ----------------
    p = page(file, 'Verification and publication', 'Verified publication and live dashboard service status',
             'Only verified release assets are published. A reused release skips new batch work and keeps the new dashboard shell; no model is promoted by ingestion.')
    # Export builds four release assets (HTML/JS/CSS/JSON); the publication
    # Lambda re-checks hashes and only then moves the "current" pointer.
    p.icon('export', 'SageMaker Processing', 'Score/FX/corridor reconciliation<br>build four release assets', 160, 520, 'sagemaker')
    p.icon('publish', 'Lambda publication', 'Verify hashes and proofs<br>commit current pointer', 560, 520, 'lambda')
    p.icon('site', 'S3 dashboard origin', 'Private encrypted bucket<br>immutable HTML/JS/CSS/JSON', 960, 520, 's3')
    p.icon('cdn', 'CloudFront', 'Serve verified business views<br>invalidate root assets', 1360, 520, 'cloudfront', side_label=True)
    p.local('browser', 'Dashboard UI', 'AML review + treasury views<br>Ingestion service diagram', 110, 880)
    p.icon('states', 'Step Functions', 'Parent + child state history<br>phase/service progress', 1760, 520, 'step_functions')
    # Chain export -> publish -> site -> cdn.
    for i, (source, target) in enumerate([('export', 'publish'), ('publish', 'site'), ('site', 'cdn')]):
        p.edge('publication_' + str(i), source, target)
    # CloudFront serves the result back to the browser (arrow loops down-left).
    p.edge('results', 'cdn', 'browser', exit=(.5, 1), entry=(1, .5))
    p.note('status', '<b>Live status</b><br>Authenticated API reads both workflow histories without payloads. The browser updates phase colours and names the active Lambda, Glue, Athena or SageMaker step every 10 seconds.', 1710, 770, 580, 180, 19)
    p.note('meaning', '<b>Progress meaning</b><br>Green: active phase. Light green: complete. Purple: reused/skipped. Red: failed/stopped. Parallel boxes may be active together. S3 writes inside acquisition and publication do not have individual byte/row counters.', 65, 1130, 2200, 150, 19)
    # --- Integrity checks (same rules as write() in the shared module) ---
    for diagram in file.findall('diagram'):
        cells = diagram.findall('./mxGraphModel/root/mxCell')
        ids = [cell.get('id') for cell in cells]
        assert len(ids) == len(set(ids))
        for cell in cells:
            if cell.get('edge') == '1':
                assert cell.get('source') in ids and cell.get('target') in ids
                assert cell.find('mxGeometry').get('relative') == '1'
    # Pretty-print and save as UTF-8 with an XML declaration.
    ET.indent(file, space='  ')
    ET.ElementTree(file).write(ROOT / 'planning/architecture/dashboard-ingestion-flow.drawio',
                               encoding='utf-8', xml_declaration=True)
    print('Dashboard ingestion diagram: 2 pages; XML and edge references validated')


if __name__ == '__main__':
    build()
