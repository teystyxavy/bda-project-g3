import ast
from pathlib import Path

import pytest

JOBS = Path(__file__).resolve().parents[1] / 'jobs'


def call_problems(path):
    """Calls to functions defined in the same script whose arguments do not fit their signature."""
    tree = ast.parse(Path(path).read_text(encoding='utf-8'))
    functions = {node.name: node.args for node in tree.body if isinstance(node, ast.FunctionDef)}
    problems = []
    for call in ast.walk(tree):
        if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Name) and call.func.id in functions):
            continue
        if any(isinstance(arg, ast.Starred) for arg in call.args) or any(keyword.arg is None for keyword in call.keywords):
            continue
        spec = functions[call.func.id]
        positional = [arg.arg for arg in spec.posonlyargs + spec.args]
        required = positional[:len(positional) - len(spec.defaults)]
        keywords = [keyword.arg for keyword in call.keywords]
        known = set(positional) | {arg.arg for arg in spec.kwonlyargs}
        supplied = set(positional[:len(call.args)]) | set(keywords)
        where = f'{call.func.id}() at line {call.lineno}'
        if len(call.args) > len(positional) and spec.vararg is None:
            problems.append(f'{where}: {len(call.args)} positional arguments but it takes {len(positional)}')
        if spec.kwarg is None and set(keywords) - known:
            problems.append(f'{where}: unexpected keyword {sorted(set(keywords) - known)}')
        if set(required) - supplied:
            problems.append(f'{where}: missing {sorted(set(required) - supplied)}')
    return problems


@pytest.mark.parametrize('script', sorted(path.name for path in JOBS.glob('*.py')))
def test_glue_job_calls_match_the_signatures_in_the_same_script(script):
    # The Spark parity tests are skipped without pyspark, so a half-merged signature would otherwise first fail inside Glue.
    assert call_problems(JOBS / script) == []
