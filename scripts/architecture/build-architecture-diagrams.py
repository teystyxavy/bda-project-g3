"""Build editable diagrams of the verified implementation; no AWS calls.

Purpose
-------
This script generates the project's architecture diagrams as draw.io
(``.drawio``) XML files, so the slides/report always show diagrams that are
consistent with what was actually built and verified. Instead of dragging
boxes around by hand in the draw.io editor, every box, icon, arrow and note
is described in Python and written out programmatically. Re-running the
script reproduces the diagrams exactly.

Where it sits in the pipeline
-----------------------------
It is documentation tooling only. It does not read data, touch S3, Glue,
Athena, SageMaker or any other AWS service. It sits *beside* the pipeline:
Kaggle + FX -> S3 raw -> Glue curate/score -> curated Parquet -> Athena SQL
and XGBoost scoring -> static S3/CloudFront dashboard, plus the Step Functions
orchestration and the separate Kinesis -> Lambda -> DynamoDB streaming demo.
Those stages are drawn here; the numbers in the notes (for example
5,078,345 reconciled rows) were copied from the verification reports.

Outputs (written under ``planning/architecture/``)
--------------------------------------------------
* ``aml-pipeline-architecture.drawio`` - 2 pages: BQ1 batch ranking and BQ2
  treasury corridor analysis (built by :func:`backbone`).
* ``aml-pipeline-aws-icons.drawio`` - 2 pages: Step Functions orchestration
  and the bounded Kinesis streaming replay (built by :func:`enhancements`).
Before overwriting an existing diagram for the first time, :func:`write`
copies the old file to ``planning/archive/architecture-proposal/`` so the
original proposal diagram is preserved.

How to run
----------
``python scripts/architecture/build-architecture-diagrams.py`` (no
arguments). Afterwards ``scripts/architecture/export-architecture.ps1`` can
turn the pages into PNG images with draw.io Desktop. The sibling script
``build-dashboard-flow.py`` imports this file (via importlib) to reuse the
:class:`Page` drawing helpers.

draw.io XML in one paragraph
----------------------------
A ``.drawio`` file is an ``<mxfile>`` containing one ``<diagram>`` per page.
Each page has an ``<mxGraphModel>`` whose ``<root>`` holds ``<mxCell>``
elements. Cell ``0`` is the invisible root and cell ``1`` is the default
layer; every other cell names its ``parent``. A *vertex* cell is a shape
(box, icon, text) with an absolute ``<mxGeometry>``; an *edge* cell is an
arrow with ``source``/``target`` ids and a relative geometry. The ``style``
attribute is a ``key=value;`` string that controls colours, fonts, etc.
"""
from pathlib import Path
import shutil
import xml.etree.ElementTree as ET

# Repository root: this file lives in <root>/scripts/architecture/, so two
# parents up from the file's folder is the project root.
ROOT = Path(__file__).resolve().parents[2]
# All diagrams are written into planning/architecture/ (kept in version control).
PLAN = ROOT / 'planning' / 'architecture'
# Fill colour per AWS service category. These are the official AWS
# Architecture Icon category colours (e.g. green for storage/S3, orange for
# compute/Lambda, pink for application integration such as Step Functions,
# SNS and EventBridge, purple for analytics such as Glue/Athena/Kinesis).
# The key is also the draw.io AWS4 stencil name used in ``resIcon=``.
COLORS = {'s3': '#7AA116', 'lambda': '#ED7100', 'dynamodb': '#C925D1',
          'cloudfront': '#8C4FFF', 'step_functions': '#E7157B', 'sns': '#E7157B',
          'eventbridge': '#E7157B', 'general': '#232F3E', 'glue': '#8C4FFF',
          'athena': '#8C4FFF', 'kinesis': '#8C4FFF', 'cloudwatch': '#E7157B'}
# Only services listed here may be drawn. Each stencil name was checked against
# the official offline draw.io AWS library, so the diagram can never reference
# a missing/incorrect icon. build-dashboard-flow.py extends this set.
VERIFIED = set(COLORS)


class Page:
    """One page (``<diagram>`` element) of a draw.io file plus drawing helpers.

    Creating a Page appends a new ``<diagram>`` to the given ``<mxfile>``,
    sets up the required root/layer cells, paints a light-grey background,
    and writes the title and subtitle notes. The helper methods
    (:meth:`vertex`, :meth:`note`, :meth:`group`, :meth:`icon`,
    :meth:`local`, :meth:`edge`) then add shapes and arrows to that page.

    Attributes:
        model: the ``<mxGraphModel>`` element (page canvas settings).
        root: the ``<root>`` element that every cell is appended to.
        cells: dict of cell id -> (x, y, width, height, parent). Used by
            :meth:`edge` to assert that arrows only connect existing cells.
    """
    def __init__(self, file, name, title, subtitle):
        """Create the page and its standard background, title and subtitle.

        Args:
            file: the ``<mxfile>`` ElementTree element to add the page to.
            name: tab name shown in draw.io; also slugified into the page id
                (lower case, spaces -> hyphens).
            title: bold heading text drawn at the top-left of the page.
            subtitle: one-line explanation drawn under the title.
        """
        # A new <diagram> element = a new tab/page in draw.io.
        diagram = ET.SubElement(file, 'diagram', id=name.lower().replace(' ', '-'), name=name)
        # Canvas settings: a fixed 2400 x 1400 page with grid, snapping guides
        # and connectable shapes enabled (dx/dy are the initial scroll offsets).
        self.model = ET.SubElement(diagram, 'mxGraphModel', dx='2800', dy='1600',
            grid='1', gridSize='10', guides='1', tooltips='1', connect='1', arrows='1',
            fold='1', page='1', pageScale='1', pageWidth='2400', pageHeight='1400',
            math='0', shadow='0')
        # draw.io requires two structural cells: id 0 is the root and id 1 is
        # the default layer that all visible shapes use as parent.
        self.root = ET.SubElement(self.model, 'root')
        ET.SubElement(self.root, 'mxCell', id='0')
        ET.SubElement(self.root, 'mxCell', id='1', parent='0')
        self.cells = {}
        # Full-page light-grey rectangle drawn first so it sits behind everything.
        self.vertex('background', '', 0, 0, 2400, 1400,
                    'rounded=0;fillColor=#F5F5F5;strokeColor=none;')
        # Title block with the group/date/verification banner as a second line.
        self.note('title', '<b>'+title+'</b><br>Group 3 | 6 October 2026 | Verified implementation',
                  40, 30, 1550, 60, 23)
        self.note('subtitle', subtitle, 40, 110, 2220, 60, 17)

    def vertex(self, id, label, x, y, w, h, style, parent='1'):
        """Add a generic shape (vertex) cell and remember its geometry.

        Args:
            id: unique cell id within the page (also used by edges).
            label: HTML label text shown inside/next to the shape.
            x, y, w, h: position and size in page pixels. When ``parent`` is a
                group, x/y are relative to that group's top-left corner.
            style: draw.io style string (``key=value;`` pairs).
            parent: id of the containing cell; ``'1'`` is the default layer.

        Returns:
            The id, so callers can write ``p.edge(..., p.icon(...), ...)``.
        """
        cell = ET.SubElement(self.root, 'mxCell', id=id, value=label, style=style,
                             vertex='1', parent=parent)
        # 'as' is a Python keyword, so it has to be passed through attrib={}.
        ET.SubElement(cell, 'mxGeometry', x=str(x), y=str(y), width=str(w),
                      height=str(h), attrib={'as': 'geometry'})
        self.cells[id] = (x, y, w, h, parent)
        return id

    def note(self, id, text, x, y, w, h, size=16, parent='1'):
        """Add a free-text note (no border) with left-aligned HTML text.

        ``size`` is the font size in points. Returns the cell id.
        """
        return self.vertex(id, text, x, y, w, h,
            f'text;html=1;align=left;verticalAlign=top;whiteSpace=wrap;rounded=0;fontSize={size};fontColor=#232F3E;fontFamily=Helvetica;spacing=8;', parent)

    def group(self, id, text, x, y, w, h):
        """Add a labelled container box (a "lane") that groups related shapes.

        ``container=1;dropTarget=1`` makes it a real draw.io container, so
        child shapes created with ``parent=id`` move together with it when
        someone edits the diagram later. The bold label sits top-left.
        Returns the cell id.
        """
        return self.vertex(id, text, x, y, w, h,
            'container=1;dropTarget=1;rounded=0;html=1;whiteSpace=wrap;fillColor=none;strokeColor=#8C98A5;strokeWidth=1.5;verticalAlign=top;align=left;spacing=15;fontSize=18;fontStyle=1;')

    def icon(self, id, title, detail, x, y, service='general', parent='1', size=78, side_label=False):
        """Add an AWS service icon with a bold title and detail text.

        Args:
            id: unique cell id.
            title: service name shown in bold (e.g. ``'AWS Glue ETL'``).
            detail: one or two lines of HTML describing what it does here.
            x, y: position (relative to ``parent`` if it is a group).
            service: key into COLORS; must be one of the VERIFIED stencils.
            parent: containing cell id (``'1'`` = page layer).
            size: icon width and height in pixels (icons are square).
            side_label: if True, draw the label *above* the icon instead of
                below it - used where a label below would collide with an
                arrow or another note.

        Returns:
            The cell id.

        Raises:
            AssertionError: if ``service`` is not a verified stencil name.
        """
        # Guard against typos or unverified icons sneaking into the diagram.
        assert service in VERIFIED
        label = f'<b>{title}</b><br>{detail}'
        # mxgraph.aws4.resourceIcon is the square AWS icon frame; resIcon picks
        # the actual service glyph and fillColor gives the category colour.
        style = f'sketch=0;outlineConnect=0;fontColor=#232F3E;fillColor={COLORS[service]};strokeColor=#ffffff;dashed=0;verticalLabelPosition=bottom;verticalAlign=top;align=center;html=1;whiteSpace=wrap;labelWidth=270;fontSize=12;fontFamily=Helvetica;aspect=fixed;shape=mxgraph.aws4.resourceIcon;resIcon=mxgraph.aws4.{service};'
        # Appending later keys overrides earlier ones in draw.io style strings,
        # so this flips the label position from below to above the icon.
        if side_label:
            style += 'labelPosition=center;verticalLabelPosition=top;verticalAlign=bottom;align=center;labelWidth=270;spacingBottom=14;'
        return self.vertex(id, label, x, y, size, size, style, parent)

    def local(self, id, title, detail, x, y, parent='1'):
        """Add an outlined box for a step that runs locally (not on AWS).

        Local steps (e.g. Kaggle download, local XGBoost training, local
        reconciliation) are deliberately drawn as plain outlined boxes so a
        viewer can tell at a glance which parts run on AWS (service icons)
        and which ran on a team member's machine. Fixed size 180 x 90.
        Returns the cell id.
        """
        return self.vertex(id, f'<b>{title}</b><br>{detail}', x, y, 180, 90,
            'rounded=0;whiteSpace=wrap;html=1;fillColor=none;strokeColor=#5A6C86;strokeWidth=1.5;align=center;verticalAlign=middle;fontSize=14;fontFamily=Helvetica;', parent)

    def edge(self, id, source, target, label='', dashed=False, error=False,
             exit=(1, .5), entry=(0, .5), diagonal=False):
        """Add an arrow between two existing cells.

        Args:
            id: unique edge id.
            source, target: ids of cells already added to this page.
            label: optional text drawn on the arrow.
            dashed: draw a dashed line (used for optional/frozen paths).
            error: draw a red dashed line (used for failure routes such as
                SNS notifications or failure archives).
            exit: (x, y) fraction of the source shape where the arrow leaves;
                (1, .5) means the middle of the right-hand side.
            entry: (x, y) fraction of the target shape where it arrives;
                (0, .5) means the middle of the left-hand side.
            diagonal: if True use a straight line instead of right-angled
                (orthogonal) routing.
        """
        # Fail fast if a typo would produce a dangling arrow.
        assert source in self.cells and target in self.cells
        style = ('edgeStyle=none;' if diagonal else 'edgeStyle=orthogonalEdgeStyle;rounded=1;orthogonalLoop=1;jettySize=auto;')
        # Grey for normal flow, AWS red (#DD344C) for error routes.
        style += f'html=1;strokeWidth=2;endArrow=block;endFill=1;exitX={exit[0]};exitY={exit[1]};entryX={entry[0]};entryY={entry[1]};exitDx=0;exitDy=0;entryDx=0;entryDy=0;strokeColor={"#DD344C" if error else "#526273"};'
        if dashed or error: style += 'dashed=1;'
        # Give labels the page background colour so text stays readable on top of the line.
        if label: style += 'labelBackgroundColor=#F5F5F5;fontSize=11;'
        attributes = dict(id=id, style=style, edge='1', parent='1', source=source, target=target)
        if label: attributes['value'] = label
        # Edge geometry is relative: draw.io computes the path from the
        # source/target shapes instead of from absolute coordinates.
        cell = ET.SubElement(self.root, 'mxCell', **attributes)
        ET.SubElement(cell, 'mxGeometry', relative='1', attrib={'as': 'geometry'})


def write(file, name):
    """Validate an ``<mxfile>`` and write it to ``planning/architecture/<name>``.

    Steps:
      1. If an older diagram with the same name exists and has not been
         archived yet, copy it once to
         ``planning/archive/architecture-proposal/<name>`` so the original
         proposal-stage diagram is kept for comparison.
      2. Structural checks on every page: cell ids are unique, every cell's
         parent exists, every edge points at existing source/target cells and
         uses a relative geometry. A failed ``assert`` stops the script
         before a broken file is written.
      3. Pretty-print (indent) the XML and write it as UTF-8 with an XML
         declaration, then print a one-line confirmation.

    Args:
        file: the ``<mxfile>`` root element built by backbone()/enhancements().
        name: output file name, e.g. ``'aml-pipeline-architecture.drawio'``.

    Side effects:
        Writes/overwrites the diagram file; may create the archive copy.
    """
    path = PLAN / name
    archive = ROOT / 'planning' / 'archive' / 'architecture-proposal' / name
    # Archive only once: later runs must not overwrite the preserved original.
    if path.exists() and not archive.exists():
        archive.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, archive)
    # --- Integrity checks so draw.io never opens a corrupt diagram ---
    for diagram in file.findall('diagram'):
        cells = diagram.findall('./mxGraphModel/root/mxCell')
        ids = [c.get('id') for c in cells]
        assert len(ids) == len(set(ids))
        for c in cells:
            # Cell '0' is the only cell allowed without a valid parent.
            assert c.get('parent') in ids or c.get('id') == '0'
            if c.get('edge') == '1':
                assert c.get('source') in ids and c.get('target') in ids
                assert c.find('mxGeometry').get('relative') == '1'
    # Indent for readable diffs in git (ElementTree.indent needs Python 3.9+).
    ET.indent(file, space='  ')
    ET.ElementTree(file).write(path, encoding='utf-8', xml_declaration=True)
    print(f'{name}: {len(file.findall("diagram"))} pages; XML and edge references validated')


def backbone():
    """Build the two-page core pipeline diagram (BQ1 and BQ2).

    Page "BQ1 batch": Kaggle IBM AML source -> local preparation (historical
    FX, validated CSVs) -> S3 raw -> Glue ETL (PySpark joins + trailing
    behaviour features) -> S3 curated Parquet. A second lane shows local
    XGBoost training -> S3 model release -> Glue frozen-model scoring ->
    local reconciliation/TreeSHAP export -> S3 dashboard origin ->
    CloudFront -> compliance analyst.

    Page "BQ2 treasury": the same curated Parquet -> Glue catalog -> Athena
    corridor SQL (daily shares + HHI concentration) -> local reconciliation
    -> S3/CloudFront dashboard -> treasury analyst.

    Coordinates are hand-tuned pixel positions on the 2400 x 1400 page.
    Writes ``aml-pipeline-architecture.drawio`` via :func:`write`.
    """
    # draw.io file root; host/version mimic the desktop app that opens it.
    file = ET.Element('mxfile', host='app.diagrams.net', version='24.7.17')
    # ---------------- Page 1: BQ1 investigation ranking ----------------
    p = Page(file, 'BQ1 batch', 'Batch backbone: investigation ranking',
        'Core pipeline: complete historical curation, frozen model inference and static insight visualization. Local steps are outlined boxes. AWS steps use service icons.')
    # Top lane: data ingestion and curation (children use lane-relative x/y).
    p.group('input_lane', 'Ingestion and curation — verified full population', 40, 195, 2320, 310)
    p.local('source', 'Kaggle IBM AML', 'HI-Small transactions<br>and account metadata', 55, 135, 'input_lane')
    p.local('prepare', 'Local preparation', 'Historical FX + validated CSVs<br>explicit reviewed uploads', 395, 135, 'input_lane')
    p.icon('raw', 'S3 raw', 'Private, encrypted<br>CSV + FX inputs', 787, 140, 's3', 'input_lane')
    p.icon('etl', 'AWS Glue ETL', 'PySpark joins + trailing features<br>date-partitioned Parquet', 1127, 140, 'glue', parent='input_lane')
    p.icon('curated', 'S3 curated', '5,078,345 reconciled rows<br>projected Glue catalog', 1467, 140, 's3', 'input_lane', side_label=True)
    # Chain the lane left-to-right: source->prepare->raw->etl->curated.
    for i, (a, b) in enumerate(zip(['source','prepare','raw','etl'], ['prepare','raw','etl','curated'])):
        p.edge('ingest'+str(i), a, b)
    p.note('curated_note', 'Account headers read by position; joins use (bank, account); string identifiers preserved. Treasury uses same-day FX, AML uses prior-day FX.', 1920, 290, 350, 170)
    # Bottom lane: model training (local) and inference (AWS) through to the viewer.
    p.group('answer_lane', 'BQ1 — local training and verified AWS inference', 40, 570, 2320, 365)
    p.local('train', 'Local XGBoost training', 'Chronological selection<br>depth-5 model frozen', 55, 165, 'answer_lane')
    p.icon('model', 'S3 model release', 'UBJ + feature schema<br>SHA-checked manifest', 447, 170, 's3', 'answer_lane')
    p.icon('score', 'AWS Glue scoring', 'Frozen CPU inference<br>all rows verified', 787, 170, 'glue', parent='answer_lane')
    p.local('export', 'Local reconciliation', 'TreeSHAP + dashboard export<br>labels omitted from queue', 1075, 165, 'answer_lane')
    p.icon('hosting', 'S3 dashboard origin', 'Private origin<br>static HTML + JSON', 1467, 170, 's3', 'answer_lane')
    p.icon('cdn', 'CloudFront', 'OAC origin access<br>public HTTPS viewers', 1807, 170, 'cloudfront', 'answer_lane')
    p.local('viewer', 'Compliance analyst', 'Test-period ranked queue<br>model/rule comparison', 2095, 165, 'answer_lane')
    # Chain train->model->score->export->hosting->cdn->viewer.
    for i, (a, b) in enumerate(zip(['train','model','score','export','hosting','cdn'], ['model','score','export','hosting','cdn','viewer'])):
        p.edge('answer'+str(i), a, b)
    # Cross-lane arrows from curated data: dashed = frozen training input
    # (training happened once, locally), solid = the data Glue scoring reads.
    p.edge('training_input', 'curated', 'train', 'Training input', True, exit=(.2,1), entry=(.5,0), diagonal=True)
    p.edge('score_input', 'curated', 'score', 'Curated input', exit=(.8,1), entry=(.5,0), diagonal=True)
    # Key findings and scope notes; figures copied from the verified evaluation.
    p.note('result', '<b>Business result</b><br>Main test top 100: model 44 positives versus rules 0. Recall@100 4.60%. Scores rank review priority; explanations describe model behaviour.', 55, 945, 1080, 115, 20)
    p.note('scope', '<b>Scope</b><br>Training, explanations, export and acceptance are local. The published dashboard is a static snapshot. No cloud training, crawler or schedule is deployed.', 1210, 945, 1080, 115, 20)
    p.note('legend', 'Solid arrows: implemented artifact/data flow. Dashed arrow: frozen training input path. All service stencils verified against the official offline draw.io library.', 55, 1185, 2200, 85)

    # ---------------- Page 2: BQ2 treasury corridors ----------------
    p = Page(file, 'BQ2 treasury', 'Batch backbone: corridor volume and concentration',
        'BQ2 supports liquidity and correspondent-bank risk decisions. High-volume corridors do not imply suspicious transactions.')
    p.group('treasury_lane', 'Verified descriptive analysis — same curated backbone as BQ1', 40, 255, 2320, 300)
    p.icon('curated', 'S3 curated Parquet', 'Verified transaction population<br>same-day amount_usd', 107, 75, 's3', 'treasury_lane')
    p.icon('catalog', 'Glue catalog', 'Explicit schema<br>date partition projection', 447, 75, 'glue', parent='treasury_lane')
    p.icon('athena', 'Athena', 'Directed bank/currency SELECT<br>daily shares + HHI', 787, 75, 'athena', parent='treasury_lane')
    p.local('verify', 'Local reconciliation', 'Counts and USD totals<br>1,082,185 daily corridor rows', 1075, 70, 'treasury_lane')
    p.icon('hosting', 'S3 dashboard origin', 'Static corridor rankings<br>daily concentration trends', 1467, 75, 's3', 'treasury_lane')
    p.icon('cdn', 'CloudFront', 'Public HTTPS dashboard<br>private S3 origin', 1807, 75, 'cloudfront', 'treasury_lane')
    p.local('viewer', 'Treasury analyst', 'Bank/currency exposure<br>liquidity review', 2095, 70, 'treasury_lane')
    # Chain curated->catalog->athena->verify->hosting->cdn->viewer.
    for i,(a,b) in enumerate(zip(['curated','catalog','athena','verify','hosting','cdn'], ['catalog','athena','verify','hosting','cdn','viewer'])):
        p.edge('treasury'+str(i),a,b)
    # The "grain" note warns that Athena rows (bank pair + currency pair per
    # day) and dashboard HHI rows (bank pair per day) are different levels.
    p.note('grain', '<b>Metric grain</b><br>Athena acceptance: daily bank pair + payment/receiving currencies. Dashboard bank-pair HHI aggregates currencies. Do not compare those row counts as the same grain.', 65, 660, 1000, 145, 22)
    p.note('numbers', '<b>Observed dataset results</b><br>USD 1.748 trillion total volume; top-five bank-pair share 6.18%. Daily share: 39.41% on September 6 versus 8.57% on September 8.', 1230, 660, 1060, 145, 22)
    p.note('fx', '<b>Source and limitations</b><br>Frankfurter covered fiat rates plus Yahoo Finance historical data for Bitcoin, Ruble and Saudi Riyal. Preserve source/observation date. Synthetic-data findings support this dataset only.', 65, 940, 2200, 140, 20)
    p.note('legend', 'Solid arrows show the catalog-mediated query and export path. The catalog stores metadata; the Parquet data remain in S3. All service stencils verified against the official offline draw.io library.', 65, 1190, 2200, 85)
    write(file, 'aml-pipeline-architecture.drawio')


def enhancements():
    """Build the two-page "X-factor" diagram (orchestration and streaming).

    Page "Batch orchestration": an approved operator starts a Standard Step
    Functions execution that validates the request, runs Glue frozen
    scoring (BQ1) and an Athena treasury query (BQ2) in parallel, then
    checks the real Glue ``_SUCCESS`` marker. Catchable failures are routed
    to SNS (red dashed arrow) before the workflow fails.

    Page "Bounded streaming": the one-off replay of 884 curated events
    (32 complete sender histories) through Kinesis -> Lambda container
    (frozen model) -> DynamoDB (state + result) -> S3 audit, followed by
    local reconciliation and disabling the consumer. Failure archive,
    CloudWatch alarm and SNS are shown as configured-but-untriggered paths.

    Writes ``aml-pipeline-aws-icons.drawio`` via :func:`write`.
    """
    file = ET.Element('mxfile', host='app.diagrams.net', version='24.7.17')
    # ---------------- Page 1: Step Functions orchestration ----------------
    p = Page(file, 'Batch orchestration', 'X-factor 1: guarded scoring and treasury orchestration',
        'Verified Standard Step Functions execution over existing curated data. The core pipeline works independently; no schedule or automatic publication is deployed.')
    p.group('workflow_lane', 'Successful reviewed workflow — 232.469 seconds', 40, 245, 2320, 310)
    p.local('operator', 'Approved operator', 'Fresh run ID / output prefixes<br>explicit execution approval', 55, 135, 'workflow_lane')
    p.icon('states', 'Step Functions', 'Validate request and contracts<br>reject existing output', 447, 140, 'step_functions', 'workflow_lane', side_label=True)
    p.icon('scoring', 'Glue scoring', 'Frozen model<br>5,078,345 scores reconciled', 1127, 140, 'glue', parent='workflow_lane')
    p.icon('ready', 'Step Functions', 'Check real success marker<br>business outputs ready', 1807, 140, 'step_functions', 'workflow_lane', side_label=True)
    p.edge('start','operator','states')
    p.edge('glue_start','states','scoring','BQ1 scoring')
    p.edge('glue_end','scoring','ready')
    # Athena branch runs in parallel with Glue scoring, so it is drawn below
    # the main lane with diagonal arrows out of and back into Step Functions.
    p.icon('athena','Athena','Parallel treasury SELECT<br>1,082,185 rows reconciled',1167,700,'athena')
    p.edge('athena_start','states','athena','BQ2 query',exit=(.5,1),entry=(0,.5),diagonal=True)
    p.edge('athena_end','athena','ready',exit=(1,.5),entry=(.5,1),diagonal=True)
    p.note('acceptance','<b>Local acceptance</b><br>Both branches reconcile before any separately reviewed export/publication. Read/notification tasks retry; compute tasks have no automatic retries.',1850,665,445,145,18)
    # Failure path: Step Functions -> SNS (red dashed = error route).
    p.icon('sns','SNS failures','Catchable failures notify<br>then workflow fails',493.5,1015,'sns',size=65)
    p.edge('error','states','sns',error=True,exit=(.5,1),entry=(.5,0))
    p.note('failure_note','Failure route has local tests; the successful AWS run did not exercise that route. No curation, retraining, scheduled trigger or dashboard publication occurs inside this workflow.',680,1010,1590,140,19)
    p.note('legend','Solid arrows: reviewed execution/control path. Red dashed arrow: configured failure route. All service stencils verified against the official offline draw.io library.',65,1235,2200,75)

    # ---------------- Page 2: bounded Kinesis streaming replay ----------------
    p = Page(file, 'Bounded streaming', 'X-factor 2: frozen-model replay with durable state and audit',
        'Completed one bounded replay of 884 curated events across 32 complete sender histories. Consumer Disabled after exact reconciliation; no second replay is authorized.')
    p.group('stream_lane','Independent Kinesis trigger path — preserves historical event time',40,245,2320,300)
    p.local('producer','Local CSV replay','Verified curated input + prior FX<br>no labels or rolling features',55,135,'stream_lane')
    p.icon('kinesis','Kinesis Data Streams','One provisioned shard<br>composite sender partition key',447,140,'kinesis',parent='stream_lane',side_label=True)
    p.icon('lambda','Lambda consumer','Frozen ECR image / model<br>preceding-window features',787,140,'lambda','stream_lane',side_label=True)
    p.icon('ddb','DynamoDB','Conditional two-Put transaction<br>32 states + 884 unique results',1127,140,'dynamodb','stream_lane')
    p.icon('audit','S3 audit','884 matching result objects<br>encrypted project prefix',1467,140,'s3','stream_lane')
    p.local('verify','Local full reconciliation','Features exact; scores within tolerance<br>916 total DynamoDB items',1755,135,'stream_lane')
    p.local('stop','Consumer Disabled','Terraform matches configuration<br>retained shard still billable',2095,135,'stream_lane')
    # Chain producer->kinesis->lambda->ddb->audit->verify->stop.
    for i,(a,b) in enumerate(zip(['producer','kinesis','lambda','ddb','audit','verify'],['kinesis','lambda','ddb','audit','verify','stop'])):
        p.edge('stream'+str(i),a,b)
    # Monitoring/failure side paths below the main lane (smaller 65 px icons).
    p.icon('archive','Isolated S3 failures','Lambda mapping failure archive<br>zero archived records',493.5,830,'s3',size=65)
    p.edge('archive_edge','kinesis','archive','Mapping failure',error=True,exit=(.5,1),entry=(.5,0))
    p.icon('monitor','CloudWatch','Structured logs + failure alarm<br>zero errors / throttles',833.5,830,'cloudwatch',size=65)
    p.edge('logs','lambda','monitor',exit=(.5,1),entry=(.5,0))
    p.icon('sns','SNS failures','Processing-failure notifications<br>no transaction threshold',1173.5,830,'sns',size=65)
    p.edge('alert','monitor','sns',error=True)
    p.note('durable','<b>Durability</b><br>Lambda commits state/result together, then writes audit. Durable duplicates repair audit without advancing history. Missing or out-of-order events fail closed. Sender versions reconciled exactly.',1480,800,795,180,20)
    p.note('limits','<b>Bounded demonstration</b><br>Actual replay took 1,049.545 seconds; this is not a measured production latency result. Dashboard remains a batch snapshot. Image scan has 8 HIGH, 6 MEDIUM and 1 LOW unresolved OS findings.',65,1085,2200,115,20)
    p.note('legend','Solid arrows: data/commit/audit or monitoring path. Red dashed arrows: configured failure routing. All service stencils verified offline. Failure archive and alarms are configured but untriggered in this successful run.',65,1240,2200,75)
    write(file,'aml-pipeline-aws-icons.drawio')


# Running the file directly regenerates both diagram files. Importing it (as
# build-dashboard-flow.py does) only defines the helpers and does not write.
if __name__ == '__main__':
    backbone()
    enhancements()
