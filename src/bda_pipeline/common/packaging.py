"""Select, check and write ``bda_pipeline`` source files for AWS packages.

Purpose
-------
Several scripts ship this package to AWS: the Glue code zip and the control
Lambda zip (``scripts/aws/prepare-ingestion.py``), the scoring parity zip
(``inference.prepare_inference``), the acquisition Lambda image
(``prepare-cloud-ingestion.py``) and the SageMaker and streaming images
(``prepare-sagemaker-container.py``, ``prepare-streaming-container.py``).
They all pick files through this module so that:

* nested subpackages are always included (a top-level ``glob('*.py')`` would
  silently drop them, and Glue would only fail at import time in AWS);
* a package is refused at build time if any module it ships imports a
  ``bda_pipeline`` module that is not inside it (``check_closure``);
* compatibility stubs left at old module paths (files that define
  ``__moved_to__``) are never shipped - deployed code imports real locations.

Only the standard library is used, so build scripts stay lightweight.
"""
import ast
import shutil
import zipfile
from pathlib import Path

PACKAGE = 'bda_pipeline'
# src/bda_pipeline (this file is src/bda_pipeline/common/packaging.py).
PACKAGE_ROOT = Path(__file__).resolve().parents[1]
# An exception handler for one of these makes the import inside the ``try``
# optional (e.g. the dashboard Lambdas fall back to flat ``dashboard_api``).
OPTIONAL_IMPORT_ERRORS = {'ImportError', 'ModuleNotFoundError'}


def is_moved_stub(path):
    """True if ``path`` is a compatibility stub that only aliases a moved module."""
    return '\n__moved_to__ = ' in '\n' + Path(path).read_text(encoding='utf-8')


def package_sources(root=PACKAGE_ROOT):
    """Every shippable ``*.py`` file under ``root``, recursively.

    Skips ``__pycache__`` folders and compatibility stubs. Sorted by POSIX
    path relative to ``root`` so zips and hash reports are reproducible.
    """
    root = Path(root)
    files = [path for path in root.rglob('*.py')
             if '__pycache__' not in path.relative_to(root).parts and not is_moved_stub(path)]
    return sorted(files, key=lambda path: path.relative_to(root).as_posix())


def module_file(name, root=PACKAGE_ROOT):
    """Source file for dotted module ``name`` (``'bda_pipeline.x.y'``), or None."""
    parts = name.split('.')
    if parts[0] != PACKAGE:
        raise ValueError(f'Not a {PACKAGE} module: {name}')
    base = Path(root).joinpath(*parts[1:])
    for candidate in [base.with_suffix('.py'), base / '__init__.py']:
        if candidate.is_file():
            return candidate
    return None


def module_name(relative):
    """``'control/ingest_control.py'`` -> ``'bda_pipeline.control.ingest_control'``."""
    parts = list(Path(relative).with_suffix('').parts)
    if parts[-1] == '__init__':
        parts.pop()
    return '.'.join([PACKAGE, *parts])


def _optional_nodes(tree):
    """Import nodes inside a ``try`` whose handlers catch ImportError."""
    optional = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Try):
            caught = set()
            for handler in node.handlers:
                types = handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
                caught |= {item.id for item in types if isinstance(item, ast.Name)}
            if caught & OPTIONAL_IMPORT_ERRORS:
                optional |= {id(child) for statement in node.body for child in ast.walk(statement)}
    return optional


def required_modules(source, relative=None):
    """Dotted ``bda_pipeline`` modules that ``source`` needs at import or call time.

    Covers absolute (``bda_pipeline.x``) and relative (``from .x import``)
    imports, including lazy imports inside functions. Imports guarded by
    ``except ImportError`` are optional and skipped. For ``from M import n``,
    ``M`` is required and so is ``M.n`` whenever ``n`` is itself a module;
    the caller resolves that second case (see ``check_closure``).

    Args:
        source: Python source text.
        relative: the file's path relative to the package root, needed to
            resolve relative imports; None for scripts outside the package.

    Returns:
        Set of ``(module, imported_names)`` pairs.
    """
    tree = ast.parse(source)
    optional = _optional_nodes(tree)
    package = module_name(relative).split('.') if relative else None
    if relative and Path(relative).name != '__init__.py':
        package = package[:-1]
    needed = set()
    for node in ast.walk(tree):
        if id(node) in optional:
            continue
        if isinstance(node, ast.Import):
            needed |= {(alias.name, ()) for alias in node.names
                       if alias.name.split('.')[0] == PACKAGE}
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                if package is None:
                    continue
                base = package[:len(package) - node.level + 1]
                module = '.'.join(base + ([node.module] if node.module else []))
            else:
                module = node.module or ''
            if module.split('.')[0] == PACKAGE:
                needed.add((module, tuple(alias.name for alias in node.names)))
    return needed


def check_closure(sources, root=PACKAGE_ROOT):
    """Refuse a package whose modules import ``bda_pipeline`` code it lacks.

    Args:
        sources: ``{path relative to the package root: source text}`` of
            exactly what will be shipped (after any ``__init__`` override).

    Raises:
        ValueError: listing every ``file -> missing module`` pair.
    """
    shipped = {module_name(relative) for relative in sources}
    missing = []
    for relative, source in sorted(sources.items()):
        for module, names in sorted(required_modules(source, relative)):
            if module not in shipped:
                missing.append(f'{relative} -> {module}')
            # ``from pkg import submodule``: the submodule must ship as well if
            # it exists in the source tree (otherwise it is an attribute).
            for name in names:
                child = f'{module}.{name}'
                if child not in shipped and module_file(child, root) is not None:
                    missing.append(f'{relative} -> {child}')
    if missing:
        raise ValueError('Package is missing imported modules: ' + '; '.join(missing))


def module_closure(entry_modules, root=PACKAGE_ROOT):
    """Files needed to import ``entry_modules``: their transitive imports plus parent ``__init__.py``.

    Imports inside ``__init__.py`` files are not followed: the top-level
    ``__init__`` only re-exports convenience helpers, and minimal Lambda
    packages replace it with a docstring.
    """
    root = Path(root)
    pending, seen = list(entry_modules), set()
    while pending:
        name = pending.pop()
        if name in seen:
            continue
        seen.add(name)
        path = module_file(name, root)
        if path is None:
            raise ValueError(f'Module not found in source tree: {name}')
        relative = path.relative_to(root)
        if relative.name == '__init__.py':
            continue
        for module, names in required_modules(path.read_text(encoding='utf-8'), relative.as_posix()):
            pending.append(module)
            pending.extend(f'{module}.{n}' for n in names if module_file(f'{module}.{n}', root) is not None)
    files = set()
    for name in seen:
        path = module_file(name, root)
        files.add(path)
        # Every enclosing package needs its __init__.py to be importable.
        for parent in path.relative_to(root).parents:
            files.add(root / parent / '__init__.py')
    return sorted(files, key=lambda path: path.relative_to(root).as_posix())


def _contents(files, root, overrides):
    root = Path(root)
    contents = {path.relative_to(root).as_posix(): path.read_bytes() for path in files}
    contents.update(overrides or {})
    check_closure({relative: data.decode('utf-8') for relative, data in contents.items()}, root)
    return contents


def write_package_zip(target, files, *, date_time, root=PACKAGE_ROOT, overrides=None):
    """Write ``files`` under ``bda_pipeline/`` in a byte-reproducible zip.

    Entries are added in sorted order with one fixed timestamp, so identical
    sources always give an identical zip (and identical hashes downstream).

    Args:
        target: zip path to create.
        files: source paths under ``root``.
        date_time: fixed ``ZipInfo`` timestamp tuple.
        overrides: ``{relative path: bytes}`` replacing or adding entries,
            e.g. a minimal ``__init__.py`` for a Lambda package.
    """
    contents = _contents(files, root, overrides)
    with zipfile.ZipFile(target, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for relative in sorted(contents):
            info = zipfile.ZipInfo(f'{PACKAGE}/{relative}', date_time=date_time)
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, contents[relative])


def copy_package(target, files, *, root=PACKAGE_ROOT, overrides=None):
    """Copy ``files`` into folder ``target`` (a Docker build context's package dir)."""
    root, target = Path(root), Path(target)
    contents = _contents(files, root, overrides)
    for relative in sorted(contents):
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        source = root / relative
        if relative in (overrides or {}) or not source.is_file():
            destination.write_bytes(contents[relative])
        else:
            shutil.copyfile(source, destination)
