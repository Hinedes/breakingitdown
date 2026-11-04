import itertools
import json
import sys

import pytest

from protocol import (
    Budget,
    CONDITIONS,
    build_manifest,
    empty_patch_sentinel,
    filter_patch,
    freeze_instances,
    grade_command,
    paired_schedule,
    prediction,
    public_task,
    run_bounded,
)


def rows(count=500):
    return [
        {
            "instance_id": f"repo__project-{index}",
            "repo": "repo/project",
            "base_commit": str(index),
            "problem_statement": f"Issue {index}",
            "patch": "gold patch must not leak",
            "test_patch": "hidden test patch must not leak",
            "hints_text": "gold-derived hints must not leak",
        }
        for index in range(count)
    ]


def test_freeze_and_schedule_are_deterministic_and_paired():
    queue = freeze_instances(rows())
    assert len(queue) == 20
    assert queue == freeze_instances(rows())
    schedule = paired_schedule(queue)
    assert set(schedule) == {row["instance_id"] for row in queue}
    assert all(sorted(order) == sorted(CONDITIONS) for order in schedule.values())
    assert set(itertools.chain.from_iterable(schedule.values())) == set(CONDITIONS)
    assert schedule == paired_schedule(queue)


def test_public_task_allowlists_only_issue_and_checkout_inputs():
    public = public_task(rows(1)[0])
    assert set(public) == {"instance_id", "repo", "base_commit", "problem_statement"}
    assert "patch" not in public and "test_patch" not in public and "hints_text" not in public


def test_budget_counts_all_roles_and_cached_tokens_are_included():
    budget = Budget(seconds=600, tokens=180_000, request_output_limit=4096)
    budget.charge(100, 20, cached_prompt_tokens=60)
    assert budget.used_tokens == 120
    assert budget.cached_prompt_tokens == 60
    with pytest.raises(ValueError):
        budget.charge(1, 4097)
    with pytest.raises(ValueError):
        budget.charge(1, 0, cached_prompt_tokens=2)


def test_grader_uses_unique_run_identity_and_fixed_queue():
    cmd = grade_command("python", "predictions.jsonl", "grade-bid-src-a1-x", ["one", "two"])
    assert "swebench.harness.run_evaluation" in cmd
    assert cmd[cmd.index("--instance_ids") + 1:cmd.index("--max_workers")] == ["one", "two"]
    assert cmd[cmd.index("--dataset_name") + 1] == "princeton-nlp/SWE-bench_Verified"
    local = grade_command("python", "predictions.jsonl", "grade-bid-src-a1-x", ["one"], "rows.jsonl")
    assert local[local.index("--dataset_name") + 1] == "rows.jsonl"
    with pytest.raises(ValueError):
        grade_command("python", "predictions.jsonl", "", ["one"])
    with pytest.raises(ValueError):
        grade_command("python", "predictions.jsonl", "run", ["one", "one"])


def test_manifest_is_deterministic_and_never_contains_gold_fields():
    queue = freeze_instances(rows())
    manifest = build_manifest(queue, created_at="2026-11-02T00:00:00Z")
    assert manifest == build_manifest(queue, created_at="2026-11-02T00:00:00Z")
    assert len(manifest["instances"]) == 20
    assert manifest["sample"]["ids_sha256"] == manifest["sample"]["ids_sha256"]
    text = json.dumps(manifest)
    for hidden in ("gold patch", "hidden test patch", "gold-derived hints", '"patch"', '"test_patch"', '"hints_text"'):
        assert hidden not in text
    assert manifest["dataset"]["parquet_sha256"].startswith("a45b1fe4")
    assert manifest["pins"]["evaluator_swebench"].startswith("02e7a74")
    assert manifest["task_inputs_sha256"] == build_manifest(queue, created_at="x")["task_inputs_sha256"]
    assert all(row["problem_statement"] not in text for row in queue)


def test_prediction_patch_excludes_bid_controls_but_keeps_project_docs():
    patch = (
        "diff --git a/.bid/states/s1 b/.bid/states/s1\n"
        "new file mode 100644\n--- /dev/null\n+++ b/.bid/states/s1\n@@ -0,0 +1 @@\n+x\n"
        "diff --git a/docs/todo.md b/docs/todo.md\n"
        "--- a/docs/todo.md\n+++ b/docs/todo.md\n@@ -1 +1 @@\n-a\n+b\n"
        "diff --git a/docs/conf.py b/docs/conf.py\n"
        "--- a/docs/conf.py\n+++ b/docs/conf.py\n@@ -1 +1 @@\n-a\n+b\n"
        "diff --git a/src/core.py b/src/core.py\n"
        "--- a/src/core.py\n+++ b/src/core.py\n@@ -1 +1 @@\n-a\n+b\n"
    )
    cleaned = filter_patch(patch)
    assert ".bid/" not in cleaned
    assert "docs/todo.md" not in cleaned
    assert "docs/conf.py" in cleaned
    assert "src/core.py" in cleaned


def test_empty_candidate_gets_a_test_neutral_sentinel():
    sentinel = empty_patch_sentinel()
    assert "new file mode" in sentinel and sentinel.endswith("\n")
    row = prediction("repo__project-1", "bid", "")
    assert row["model_patch"] == sentinel
    kept = prediction("repo__project-1", "bid", "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-a\n+b\n")
    assert ".bid-empty-patch" not in kept["model_patch"]


def test_external_deadline_kills_the_process_tree():
    result = run_bounded([sys.executable, "-c", "import time; time.sleep(10)"], 0.1)
    assert result["timed_out"] is True
