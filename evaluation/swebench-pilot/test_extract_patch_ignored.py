"""Regression: patch extraction must respect .gitignore, not force ignored artifacts.

Builds a throwaway repo with an ignored ``__pycache__`` payload, a legitimate
untracked source file, a tracked binary change, a tracked deletion and BID
control files, extracts the patch with the production container extractor, then
proves the patch applies against a fresh base tree that already contains the
ignored cache in the same task image.

The pre-fix policy (``git add -N -f``) includes the ignored ``.pyc`` files; their
"create file" hunks fail against a tree that already has them. The fixed policy
(``git add -N``) excludes them and still carries every legitimate change.

Runs Docker only; no model, no network.
"""

import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

try:
    import pytest  # optional: the eval venv may not ship pytest
except ImportError:  # pragma: no cover - exercised on the controller venv
    pytest = None

sys.path.insert(0, str(Path(__file__).resolve().parent))
import protocol  # noqa: E402
import runner  # noqa: E402

IMAGE = os.environ.get("BID_TEST_IMAGE", "swebench/sweb.eval.x86_64.django_1776_django-11133:latest")
PYC_A = "django/__pycache__/validators.cpython-36.pyc"
PYC_B = "django/core/__pycache__/validators.cpython-36.pyc"


def _image_ready():
    if not shutil.which("docker"):
        return False
    return subprocess.run(["docker", "image", "inspect", IMAGE],
                          capture_output=True).returncode == 0


pytestmark = ([pytest.mark.skipif(not _image_ready(), reason="docker or test image unavailable")]
              if pytest is not None else [])


def _git(args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True)


def _tree_hashes(root):
    out = {}
    for path in sorted(Path(root).rglob("*")):
        if path.is_file() and ".git" not in path.parts:
            out[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


def _build_repo(root):
    repo = root / "repo"
    (repo / "django" / "core").mkdir(parents=True, exist_ok=True)
    (repo / "legacy").mkdir(exist_ok=True)
    (repo / "assets").mkdir(exist_ok=True)
    (repo / ".gitignore").write_text("__pycache__/\n*.pyc\n", encoding="utf-8")
    (repo / "django" / "core" / "validators.py").write_text("VALUE = 1\n", encoding="utf-8")
    (repo / "legacy" / "old_module.py").write_text("OLD = True\n", encoding="utf-8")
    (repo / "assets" / "logo.bin").write_bytes(b"\x00base-binary")
    _git(["init", "-q"], repo)
    _git(["config", "user.email", "t@example.invalid"], repo)
    _git(["config", "user.name", "t"], repo)
    _git(["add", "-A"], repo)
    _git(["commit", "-qm", "base"], repo)
    base = _git(["rev-parse", "HEAD"], repo).stdout.strip()

    (repo / "django" / "core" / "validators.py").write_text("VALUE = 2\n", encoding="utf-8")
    (repo / "legacy" / "old_module.py").unlink()
    (repo / "assets" / "logo.bin").write_bytes(b"\x00changed-binary\xff")
    (repo / "fix_new.py").write_text("FIX = True\n", encoding="utf-8")
    (repo / PYC_A).parent.mkdir(parents=True, exist_ok=True)
    (repo / PYC_A).write_bytes(b"\x00ignored-bytecode-a")
    (repo / PYC_B).parent.mkdir(parents=True, exist_ok=True)
    (repo / PYC_B).write_bytes(b"\x00ignored-bytecode-b")
    (repo / ".bid").mkdir()
    (repo / ".bid" / "current").write_text("task\n", encoding="utf-8")
    (repo / "docs").mkdir()
    (repo / "docs" / "todo.md").write_text("controller note\n", encoding="utf-8")
    return repo, base


def _extract_with_flag(image, checkout, base_commit, force):
    checkout = Path(checkout).resolve()
    excludes = [f":(exclude){path.rstrip('/')}" for path in protocol.CONTROL_PATHS]
    excludes.append(f":(exclude){protocol.EMPTY_PATCH_PATH}")
    pathspec = " ".join(f"'{item}'" for item in (".", *excludes))
    flag = "-f " if force else ""
    script = (f"git -c safe.directory='*' -c core.fileMode=false add -N {flag}-- {pathspec} && "
              f"git -c safe.directory='*' -c core.fileMode=false diff --binary {base_commit} -- .")
    result = subprocess.run(
        ["docker", "run", "--rm", "--network", "none", "--cap-drop", "ALL",
         "--security-opt", "no-new-privileges", "--read-only",
         "--tmpfs", "/tmp:rw,noexec,nosuid",
         "-e", "HOME=/tmp",
         "-v", f"{checkout.parent}:/work", "-w", f"/work/{checkout.name}",
         image, "sh", "-lc", script],
        capture_output=True, text=True, timeout=180, env=os.environ)
    assert result.returncode == 0, result.stderr[-800:]
    return result.stdout


def _fresh_base_with_cache(root, repo, base):
    fresh = root / "fresh"
    subprocess.run(["git", "clone", "-q", str(repo), str(fresh)], check=True,
                   capture_output=True, text=True)
    _git(["checkout", "-q", base], fresh)
    for rel in (PYC_A, PYC_B):
        (fresh / rel).parent.mkdir(parents=True, exist_ok=True)
        (fresh / rel).write_bytes(b"prebuilt-image-bytecode")
    return fresh


def _apply_check(root, fresh, patch_text):
    patch_dir = root / "patches"
    patch_dir.mkdir(exist_ok=True)
    name = f"p-{hashlib.sha256(patch_text.encode()).hexdigest()[:8]}.diff"
    (patch_dir / name).write_text(patch_text, encoding="utf-8")
    result = subprocess.run(
        ["docker", "run", "--rm", "--network", "none", "--cap-drop", "ALL",
         "--security-opt", "no-new-privileges",
         "-v", f"{fresh}:/testbed", "-v", f"{patch_dir}:/patches:ro",
         "-w", "/testbed", IMAGE, "sh", "-lc", f"git apply --check /patches/{name}"],
        capture_output=True, text=True, timeout=180, env=os.environ)
    return result.returncode, (result.stderr or result.stdout).strip()[-800:]


def test_ignored_artifacts_excluded_and_patch_applies(tmp_path):
    repo, base = _build_repo(tmp_path)
    # The pre-fix extractor force-adds ignored files into the index; give each
    # policy its own copy so one run cannot contaminate the other.
    repo_new = tmp_path / "repo_new"
    shutil.copytree(repo, repo_new, symlinks=True)
    before = _tree_hashes(repo_new)

    old_patch = _extract_with_flag(IMAGE, repo, base, force=True)
    new_patch = runner.extract_patch_in_container(IMAGE, repo_new, base, env=os.environ)

    assert "__pycache__" in old_patch, "pre-fix policy should include ignored bytecode"
    assert "__pycache__" not in new_patch and not new_patch.count(".pyc"), "ignored artifacts leaked"

    for needle in ("fix_new.py", "validators.py", "logo.bin", "old_module.py", "GIT binary patch"):
        assert needle in new_patch, f"legitimate change lost: {needle}"
    assert "legacy/old_module.py" in new_patch, "tracked deletion lost"
    assert ".bid/" not in new_patch and "docs/todo.md" not in new_patch, "control path leaked"

    after = _tree_hashes(repo_new)
    assert before == after, "extraction modified workspace files"

    fresh = _fresh_base_with_cache(tmp_path, repo, base)
    old_rc, old_err = _apply_check(tmp_path, fresh, old_patch)
    new_rc, new_err = _apply_check(tmp_path, fresh, new_patch)
    assert old_rc != 0, "pre-fix patch unexpectedly applied"
    assert "already exists" in old_err or "Skipping patch" in old_err, old_err
    assert new_rc == 0, f"fixed patch does not apply: {new_err}"


def test_extraction_does_not_touch_global_git_config():
    def global_config():
        out = subprocess.run(["docker", "run", "--rm", "--network", "none", IMAGE,
                              "git", "config", "--global", "--list"],
                             capture_output=True, text=True, timeout=120, env=os.environ)
        return out.returncode, out.stdout

    before = global_config()
    root = Path(os.environ.get("TMPDIR", "/tmp")) / f"bid-cfg-{os.getpid()}"
    shutil.rmtree(root, ignore_errors=True)
    try:
        repo, base = _build_repo(root)
        runner.extract_patch_in_container(IMAGE, repo, base, env=os.environ)
    finally:
        shutil.rmtree(root, ignore_errors=True)
    assert global_config() == before, "extraction changed image/global git config"


if __name__ == "__main__":
    import tempfile

    if not _image_ready():
        raise SystemExit(f"SKIP: docker or image {IMAGE} unavailable")
    with tempfile.TemporaryDirectory() as tmp:
        test_ignored_artifacts_excluded_and_patch_applies(Path(tmp))
    test_extraction_does_not_touch_global_git_config()
    print("EXTRACT_REGRESSION_OK")
