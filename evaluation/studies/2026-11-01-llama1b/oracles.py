"""Hidden, deterministic artifact and behavior checks for the frozen tasks."""

import ast
import hashlib
import json
import pathlib

from sandbox import run_python


TASKS = ("easy", "pipeline", "repo-fix")
SAMPLE_SHA256 = "7ff07e774b04508eca84701f209cfd95d480d835aae07a748a12572da904b3bd"


def _safe_file(root, relative):
    root = pathlib.Path(root).resolve()
    path = root / relative
    for part in (path, *path.parents):
        if part == root.parent:
            break
        if part.is_symlink():
            return None
    try:
        path.resolve(strict=True).relative_to(root)
    except (OSError, ValueError):
        return None
    return path if path.is_file() else None


def _has_test_function(path):
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, UnicodeError):
        return False
    return any(
        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name.startswith("test_")
        for node in tree.body
    )


def _check(name, passed, detail=""):
    return {"name": name, "pass": bool(passed), "detail": detail}


def _behavior(root, code, python_home, temp_root, expected):
    result = run_python(root, code, python_home, temp_root)
    if result["timed_out"]:
        return _check("behavior", False, "sandbox timeout")
    if result["returncode"]:
        return _check("behavior", False, "sandbox/import/process failure")
    try:
        actual = json.loads(result["stdout"].strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError):
        return _check("behavior", False, "no JSON behavior result")
    return _check("behavior", actual == expected, "output matched" if actual == expected else "output mismatch")


def evaluate(task, workspace, python_home, temp_root):
    root = pathlib.Path(workspace)
    files = {}
    checks = []

    if task == "easy":
        source = _safe_file(root, "word_stats.py")
        if source:
            try:
                names = {
                    node.name for node in ast.parse(source.read_text(encoding="utf-8")).body
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                }
            except (OSError, SyntaxError, UnicodeError):
                names = set()
        else:
            names = set()
        checks.append(_check("word_stats.py defines both functions", {"count_words", "longest_word"} <= names))
        tests = _safe_file(root, "tests/test_word_stats.py")
        checks.append(_check("collected-style test function", bool(tests and _has_test_function(tests))))
        readme = _safe_file(root, "README.md")
        checks.append(_check("README example", bool(readme and all(x in readme.read_text(encoding="utf-8", errors="replace") for x in ("count_words", "longest_word")))))
        if source:
            code = (
                "import json,sys; sys.path.insert(0,'/work'); import word_stats; "
                "print(json.dumps([word_stats.count_words(' alpha\\tbeta\\n'), "
                "word_stats.count_words(' \\n'), word_stats.longest_word('blue teal'), "
                "word_stats.longest_word('')]))"
            )
            checks.append(_behavior(root, code, python_home, temp_root, [2, 0, "blue", None]))
        else:
            checks.append(_check("behavior", False, "word_stats.py missing"))

    elif task == "pipeline":
        required = ("sales_loader.py", "sales_filter.py", "sales_summary.py", "sales_pipeline.py")
        for name in required:
            files[name] = _safe_file(root, name)
            checks.append(_check(f"artifact {name}", files[name] is not None))
        tests = _safe_file(root, "tests/test_sales_pipeline.py")
        checks.append(_check("collected-style test function", bool(tests and _has_test_function(tests))))
        sample = _safe_file(root, "sample_sales.csv")
        digest = hashlib.sha256(sample.read_bytes()).hexdigest() if sample else ""
        checks.append(_check("seed CSV preserved", bool(sample and digest == SAMPLE_SHA256)))
        if all(files.values()) and sample:
            code = (
                "import json,sys; sys.path.insert(0,'/work'); "
                "from sales_pipeline import build_sales_report; "
                "print(json.dumps(build_sales_report('/work/sample_sales.csv','2026-01-02','2026-01-06')))"
            )
            expected = [
                {"sku": "A-1", "units": 3, "revenue_cents": 300},
                {"sku": "B-2", "units": 3, "revenue_cents": 750},
                {"sku": "C-3", "units": 2, "revenue_cents": 198},
            ]
            checks.append(_behavior(root, code, python_home, temp_root, expected))
        else:
            checks.append(_check("behavior", False, "required module or sample missing"))

    elif task == "repo-fix":
        source = _safe_file(root, "range_utils.py")
        if source:
            try:
                names = {
                    node.name for node in ast.parse(source.read_text(encoding="utf-8")).body
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                }
            except (OSError, SyntaxError, UnicodeError):
                names = set()
        else:
            names = set()
        checks.append(_check("range_utils.py defines merge_ranges", "merge_ranges" in names))
        tests = _safe_file(root, "tests/test_range_utils.py")
        checks.append(_check("collected-style test function", bool(tests and _has_test_function(tests))))
        if source:
            code = (
                "import json,sys; sys.path.insert(0,'/work'); from range_utils import merge_ranges; "
                "print(json.dumps([merge_ranges([(8,10),(1,4),(3,6)]), "
                "merge_ranges([(5,9),(2,3),(7,11)]), merge_ranges([(1,4),(5,7)]), merge_ranges([])]))"
            )
            expected = [[[1, 6], [8, 10]], [[2, 3], [5, 11]], [[1, 4], [5, 7]], []]
            checks.append(_behavior(root, code, python_home, temp_root, expected))
        else:
            checks.append(_check("behavior", False, "range_utils.py missing"))
    else:
        raise ValueError(f"unknown frozen task: {task}")

    passed = sum(check["pass"] for check in checks)
    return {
        "task": task,
        "checks_passed": passed,
        "checks_total": len(checks),
        "binary_pass": passed == len(checks),
        "checks": checks,
    }


def sample_sha256(path):
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()
