"""Regression: an official FAIL_TO_PASS list that omits a test the test patch
creates makes the base commit resolve.

`django__django-10097` is the concrete case. `tests/validators/tests.py` builds
TEST_DATA from `invalid_urls.txt`/`valid_urls.txt` at import time and names each
case `test_<validator>_raises_error_<enumeration index>`. The test patch appends
six URLs, which creates `test_URLValidator_raises_error_352..357`. Those six
fail on the base commit and pass with the gold patch (base run: 51 failures,
gold run: 45, difference is exactly those six). The frozen/official
FAIL_TO_PASS snapshot holds 438 names and none of the six, so official v5
grading sees every expected test passing at base and resolves an empty patch.

Run against the pinned evaluator:
    SWEBENCH_SRC=<...>/source/SWE-Bench python test_grading_coverage.py
"""

import os
import sys
from pathlib import Path

DEFAULT_SRC = "/home/iclab/bid-benchmarks/swebench-verified20-2026-11-02/source/SWE-bench"

F2P_PASSED = "test_ascii_validator (auth_tests.test_validators.UsernameValidatorsTests)"
P2P_PASSED = "test_defaults (str.tests.SimpleTests)"
# Stands in for the six tests the test patch creates; fails at base, passes at gold.
PATCH_CREATED_FAILING = "test_URLValidator_raises_error_352 (validators.tests.TestSimpleValidators)"


def _load_grading():
    src = os.environ.get("SWEBENCH_SRC", DEFAULT_SRC)
    if not (Path(src) / "swebench" / "harness" / "grading.py").exists():
        return None
    sys.path.insert(0, src)
    from swebench.harness import grading

    return grading


grading = _load_grading()


def _require_pinned_evaluator():
    if grading is None:
        import pytest

        pytest.skip("set SWEBENCH_SRC to the pinned SWE-Bench checkout")


def _spec(f2p):
    from swebench.types import TestSpec

    return TestSpec(
        instance_id="django__django-10097",
        image="swebench/sweb.eval.x86_64.django_1776_django-10097:latest",
        eval_script_list=["true"],
        repo="django/django",
        version="2.2",
        FAIL_TO_PASS=list(f2p),
        PASS_TO_PASS=[P2P_PASSED],
        log_parser="parse_log_django",
        eval_type="pass_and_fail",
    )


def _write_log(tmp_path, with_fail_line):
    lines = [f"{F2P_PASSED} ... ok", f"{P2P_PASSED} ... ok"]
    if with_fail_line:
        lines += [f"{PATCH_CREATED_FAILING} ... FAIL", f"FAIL: {PATCH_CREATED_FAILING}"]
    body = "\n".join(lines)
    log = tmp_path / "test_output.txt"
    log.write_text(
        ">>>>> Start Test Output\n"
        f"{body}\n"
        "Ran 3 tests\n"
        f"FAILED (failures={1 if with_fail_line else 0})\n"
        ">>>>> End Test Output\n"
        ">>>>> Test Exit Code: 1\n"
    )
    return log


def _resolved(tmp_path, f2p, with_fail_line=True):
    log = _write_log(tmp_path, with_fail_line)
    spec = _spec(f2p)
    prediction = {"instance_id": spec.instance_id, "model_name_or_path": "gate-base", "model_patch": ""}
    report = grading.get_eval_report(spec, prediction, str(log), include_tests_status=True)
    return report[spec.instance_id], spec, log


def test_parser_sees_the_patch_created_failure():
    _require_pinned_evaluator()
    tmp_path = Path(os.environ["TMPDIR"]) if os.environ.get("TMPDIR") else Path("/tmp")
    tmp_path.mkdir(exist_ok=True)
    _, spec, log = _resolved(tmp_path, [F2P_PASSED])
    status_map, found = grading.get_logs_eval(spec, str(log))
    assert found is True
    assert status_map[PATCH_CREATED_FAILING] == "FAILED"


def test_omitted_expected_test_resolves_base_and_declared_test_does_not():
    _require_pinned_evaluator()
    tmp_path = Path(os.environ["TMPDIR"]) if os.environ.get("TMPDIR") else Path("/tmp")
    tmp_path.mkdir(exist_ok=True)

    report, _, _ = _resolved(tmp_path, [F2P_PASSED])
    assert report["resolved"] is True, "official list omits the failing test -> base resolves"

    report, _, _ = _resolved(tmp_path, [F2P_PASSED, PATCH_CREATED_FAILING])
    assert report["resolved"] is False, "declaring the failing test F2P -> base does not resolve"


if __name__ == "__main__":
    test_parser_sees_the_patch_created_failure()
    test_omitted_expected_test_resolves_base_and_declared_test_does_not()
    print("OK: parser sees the failure; omission alone flips resolved to True")
