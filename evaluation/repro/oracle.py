import ast
import pathlib
import sys


def check(workspace):
    root = pathlib.Path(workspace)
    source = (root / "greet.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    function = next(
        (node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "greet"),
        None,
    )
    assert function is not None, "greet.py must define greet"
    assert (root / "tests" / "test_greet.py").is_file(), "missing tests/test_greet.py"
    test_tree = ast.parse((root / "tests" / "test_greet.py").read_text(encoding="utf-8"))
    assert any(
        isinstance(node, ast.FunctionDef) and node.name.startswith("test_")
        for node in test_tree.body
    ), "test_greet.py must define a test function"
    readme = (root / "README.md").read_text(encoding="utf-8")
    assert 'greet("Ada")' in readme, "README.md must show a greet usage example"
    namespace = {}
    exec(compile(tree, str(root / "greet.py"), "exec"), namespace)
    assert namespace["greet"]("Ada") == "Hello, Ada!", "greet output mismatch"


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: python oracle.py WORKSPACE")
    check(sys.argv[1])
    print("PASS: greet-module oracle")
