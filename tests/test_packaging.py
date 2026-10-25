"""Every AWS package ships the modules its entry points and imports need."""
import ast
import hashlib
import importlib.util
import json
import re
import sys
import zipfile
from pathlib import Path

import pytest

from bda_pipeline.common.packaging import (PACKAGE_ROOT, check_closure, is_moved_stub, module_closure, module_file,
                                           package_sources, required_modules, write_package_zip)

ROOT = Path(__file__).resolve().parents[1]
IMAGE_URI = '588738581042.dkr.ecr.ap-southeast-1.amazonaws.com/is459-g3-training@sha256:' + 'a' * 64
# Heavy libraries the small Lambda packages must not need.
HEAVY = {'xgboost', 'pyarrow', 'sklearn'}


def load_script(relative):
    spec = importlib.util.spec_from_file_location(Path(relative).stem.replace('-', '_'), ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_main(module, argv, monkeypatch):
    monkeypatch.setattr(sys, 'argv', ['prepare', *argv])
    module.main()


def tree(root, files):
    for relative, text in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return root


def zip_modules(path, prefix='bda_pipeline/'):
    with zipfile.ZipFile(path) as archive:
        return {name[len(prefix):]: archive.read(name).decode() for name in archive.namelist() if name.startswith(prefix)}


def folder_modules(path):
    return {p.relative_to(path).as_posix(): p.read_text() for p in path.rglob('*.py')}


def entry_modules(path):
    """``bda_pipeline`` modules an entry script outside the package imports."""
    return {module for module, _ in required_modules(Path(path).read_text())}


def ships(modules, dotted):
    relative = '/'.join(dotted.split('.')[1:])
    return f'{relative}.py' in modules or f'{relative}/__init__.py' in modules


def third_party(modules):
    names = set()
    for source in modules.values():
        names |= set(re.findall(r'^\s*(?:import|from)\s+([a-zA-Z_]\w*)', source, re.M))
    return names


def handler_module(terraform, pattern=r'handler\s*=\s*"([\w.]+)\.\w+"'):
    return re.search(pattern, (ROOT / terraform).read_text()).group(1)


def fake_model(directory):
    """Smallest artifact set that passes ``verify_artifacts`` (no XGBoost needed)."""
    directory.mkdir()
    (directory / 'm.ubj').write_bytes(b'model')
    for name in ['comparison.json', 'features.json']:
        (directory / name).write_text('{}')
    (directory / 'selection.json').write_text(json.dumps({'selected': {'model_file': 'm.ubj'}}))
    names = ['comparison.json', 'features.json', 'selection.json', 'm.ubj']
    manifest = dict(schema_version=1, model_file='m.ubj',
                    files={n: hashlib.sha256((directory / n).read_bytes()).hexdigest() for n in names})
    (directory / 'model-manifest.json').write_text(json.dumps(manifest))
    return directory


@pytest.fixture(scope='module')
def ingestion(tmp_path_factory):
    work = tmp_path_factory.mktemp('ingestion')
    wheels = work / 'wheels.zip'
    with zipfile.ZipFile(wheels, 'w') as archive:
        archive.writestr('placeholder.txt', 'wheels')
    output = work / 'out'
    load_script('scripts/aws/prepare-ingestion.py').prepare(output, fake_model(work / 'model'), wheels, IMAGE_URI)
    return output


# --- helper behaviour -------------------------------------------------------

def test_sources_recurse_and_skip_caches_and_stubs(tmp_path):
    root = tree(tmp_path / 'bda_pipeline', {
        '__init__.py': '', 'a.py': '', 'sub/__init__.py': '', 'sub/b.py': '',
        '__pycache__/a.py': '', 'old.py': "import sys\n__moved_to__ = 'bda_pipeline.sub.b'\n"})
    assert [p.relative_to(root).as_posix() for p in package_sources(root)] == [
        '__init__.py', 'a.py', 'sub/__init__.py', 'sub/b.py']


def test_closure_follows_lazy_imports_and_adds_parent_packages(tmp_path):
    root = tree(tmp_path / 'bda_pipeline', {
        '__init__.py': 'from .heavy import x\n', 'heavy.py': 'import xgboost\n',
        'entry.py': 'from .sub.b import y\n',
        'sub/__init__.py': '', 'sub/b.py': 'def f():\n    from ..c import z\n', 'c.py': 'z = 1\n',
        'unused.py': ''})
    assert [p.relative_to(root).as_posix() for p in module_closure(['bda_pipeline.entry'], root)] == [
        '__init__.py', 'c.py', 'entry.py', 'sub/__init__.py', 'sub/b.py']


def test_check_rejects_missing_lazy_import_and_allows_optional_fallback(tmp_path):
    root = tree(tmp_path / 'bda_pipeline', {'__init__.py': '', 'a.py': '', 'b.py': ''})
    check_closure({'__init__.py': '', 'a.py': (
        'try:\n    from bda_pipeline.missing import x\nexcept ImportError:\n    from missing import x\n')}, root)
    with pytest.raises(ValueError, match=r'a.py -> bda_pipeline.b'):
        check_closure({'__init__.py': '', 'a.py': 'def f():\n    from .b import y\n'}, root)
    with pytest.raises(ValueError, match=r'a.py -> bda_pipeline.b'):
        check_closure({'__init__.py': '', 'a.py': 'from . import b\n'}, root)


def test_zip_is_reproducible_and_refuses_incomplete_packages(tmp_path):
    root = tree(tmp_path / 'bda_pipeline', {'__init__.py': '', 'a.py': 'from .b import x\n', 'b.py': 'x = 1\n'})
    first, second = tmp_path / 'first.zip', tmp_path / 'second.zip'
    for target in [first, second]:
        write_package_zip(target, package_sources(root), date_time=(2026, 1, 1, 0, 0, 0), root=root)
    assert first.read_bytes() == second.read_bytes()
    with pytest.raises(ValueError, match='missing'):
        write_package_zip(tmp_path / 'bad.zip', [root / '__init__.py', root / 'a.py'],
                          date_time=(2026, 1, 1, 0, 0, 0), root=root)
    assert not (tmp_path / 'bad.zip').exists()


# --- real packages ----------------------------------------------------------

def test_glue_zip_ships_every_module_the_scoring_job_imports(ingestion):
    modules = zip_modules(ingestion / 'bda_pipeline.zip')
    assert set(modules) == {p.relative_to(PACKAGE_ROOT).as_posix() for p in package_sources()}
    for module in entry_modules(ROOT / 'jobs/score_transactions.py'):
        assert ships(modules, module), module


def test_control_lambda_ships_its_terraform_handler_without_heavy_dependencies(ingestion):
    modules = zip_modules(ingestion / 'control.zip')
    assert ships(modules, handler_module('infra/ingestion/main.tf'))
    assert modules['__init__.py'] == '"""Ingestion control package."""\n'
    assert not third_party(modules) & (HEAVY | {'pandas', 'numpy'})


def test_acquisition_lambda_ships_its_image_command(tmp_path):
    output = tmp_path / 'context'
    load_script('scripts/aws/prepare-cloud-ingestion.py').prepare(output)
    modules = folder_modules(output / 'bda_pipeline')
    command = re.search(r'CMD \["([\w.]+)\.\w+"\]', (ROOT / 'cloud-ingestion/Dockerfile').read_text()).group(1)
    assert ships(modules, command)
    assert not third_party(modules) & HEAVY
    recorded = json.loads((output / 'build-context.json').read_text())
    assert {f'bda_pipeline/{name}' for name in modules} <= set(recorded)


@pytest.mark.parametrize('script, archive, terraform', [
    ('scripts/aws/prepare-dashboard-api.py', 'dashboard-api.zip', 'infra/dashboard-control/main.tf'),
    ('scripts/aws/prepare-dashboard-status.py', 'dashboard-status.zip', 'infra/dashboard-status/main.tf')])
def test_dashboard_lambdas_ship_flat_handler_modules(tmp_path, script, archive, terraform):
    manifest = load_script(script).prepare(tmp_path / 'out')
    with zipfile.ZipFile(tmp_path / 'out' / archive) as package:
        names = package.namelist()
    assert names == manifest['members'] and all('/' not in name for name in names)
    assert handler_module(terraform) + '.py' in names


def test_sagemaker_image_ships_its_entry_point_imports(tmp_path, monkeypatch):
    output = tmp_path / 'context'
    run_main(load_script('scripts/aws/prepare-sagemaker-container.py'), ['--output', str(output)], monkeypatch)
    modules = folder_modules(output / 'bda_pipeline')
    check_closure(modules)
    for entry in ['sagemaker/train.py', 'sagemaker/pipeline_worker.py']:
        for module in entry_modules(ROOT / entry):
            assert ships(modules, module), (entry, module)


def test_streaming_image_ships_its_handler_imports(tmp_path, monkeypatch):
    script = load_script('scripts/streaming/prepare-streaming-container.py')
    # The fixture model cannot match the real release's trusted manifest hash.
    monkeypatch.setattr(script, 'verify_artifacts', lambda *args: None)
    output = tmp_path / 'context'
    run_main(script, ['--artifacts', str(fake_model(tmp_path / 'model')), '--output', str(output)], monkeypatch)
    modules = folder_modules(output / 'bda_pipeline')
    check_closure(modules)
    for module in entry_modules(ROOT / 'streaming/handler.py'):
        assert ships(modules, module), module


def test_whole_source_tree_has_no_dangling_imports():
    check_closure({p.relative_to(PACKAGE_ROOT).as_posix(): p.read_text() for p in package_sources()})


# Pipeline order: a subpackage may import only from itself or earlier stages.
LAYERS = ['common', 'ingestion', 'features', 'baseline', 'treasury', 'modeling', 'scoring', 'dashboards', 'control']


def test_subpackages_only_import_earlier_pipeline_stages():
    violations = []
    for path in package_sources():
        relative = path.relative_to(PACKAGE_ROOT)
        if len(relative.parts) < 2:
            continue  # Top-level __init__ re-exports conveniences.
        own = LAYERS.index(relative.parts[0])
        for module, _ in required_modules(path.read_text(), relative.as_posix()):
            parts = module.split('.')
            if len(parts) > 1 and parts[1] in LAYERS and LAYERS.index(parts[1]) > own:
                violations.append(f'{relative.as_posix()} -> {module}')
    assert not violations


# Top-level module paths retired by the stage reorganisation.
RETIRED = {'acquisition', 'analytics', 'batch', 'cloud_acquisition', 'cloud_scores', 'dashboard',
           'dashboard_api', 'dashboard_exploration', 'dashboard_sessions', 'dashboard_status',
           'dataset_options', 'fx', 'inference', 'ingest_compute', 'ingest_contract', 'ingest_control',
           'model', 'model_dashboard', 'orchestration', 'release', 'sagemaker_training', 'stage_cache',
           'streaming', 'transform', 'treasury_verification'}
CODE_DIRS = ['src', 'scripts', 'tests', 'jobs', 'sagemaker', 'streaming', 'infra', 'cloud-ingestion']


def retired_references(path):
    """Imports and dotted string targets (e.g. monkeypatch paths) that use a retired module path."""
    tree = ast.parse(path.read_text())
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and not node.level and node.module:
            names = node.module.split('.')
            if names[0] == 'bda_pipeline' and (names[1:2] and names[1] in RETIRED
                                               or len(names) == 1 and {a.name for a in node.names} & RETIRED):
                found.append(f'{path.relative_to(ROOT)}:{node.lineno}')
        elif isinstance(node, ast.Import):
            found += [f'{path.relative_to(ROOT)}:{node.lineno}' for a in node.names
                      if a.name.split('.')[:1] == ['bda_pipeline'] and a.name.split('.')[1:2] and a.name.split('.')[1] in RETIRED]
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            match = re.fullmatch(r'bda_pipeline\.(\w+)(\.\w+)*', node.value)
            if match and match.group(1) in RETIRED:
                found.append(f'{path.relative_to(ROOT)}:{node.lineno}')
    return found


def test_no_code_uses_retired_module_paths():
    found = []
    for directory in CODE_DIRS:
        for path in sorted((ROOT / directory).rglob('*.py')):
            if '__pycache__' in path.parts or path.parent == PACKAGE_ROOT and is_moved_stub(path):
                continue
            found += retired_references(path)
    assert not found
