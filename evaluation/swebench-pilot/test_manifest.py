import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import protocol

MANIFEST = Path(__file__).with_name("manifest.json")


def load():
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def test_checked_in_manifest_identity_and_self_hash():
    manifest = load()
    ids = [row["instance_id"] for row in manifest["instances"]]
    assert len(ids) == 20 == len(set(ids))
    assert ids == sorted(ids)
    assert manifest["sample"]["ids_sha256"] == protocol.ids_sha256(ids)
    derived = protocol.seal_manifest({k: v for k, v in manifest.items() if k != "manifest_sha256"})
    assert derived["manifest_sha256"] == manifest["manifest_sha256"]
    assert manifest["dataset"]["revision"] == protocol.DATASET_REVISION
    assert manifest["dataset"]["parquet_sha256"] == protocol.DATASET_PARQUET_SHA256
    assert manifest["pins"]["evaluator_swebench"] == protocol.EVALUATOR_COMMIT
    assert manifest["pins"]["task_repo"] == protocol.TASK_REPO_COMMIT
    assert manifest["pins"]["mini_swe_agent"] == protocol.MINI_COMMIT
    assert manifest["pins"]["model_sha256"] == protocol.MODEL_SHA256
    assert manifest["pins"]["llama_cpp"] == protocol.LLAMA_CPP_COMMIT
    assert manifest["budgets"]["provisional_batch"] == 1
    assert manifest["budgets"]["max_searches_per_worker"] == 0
    assert manifest["budgets"]["protocol_grammar"] is False
    assert manifest["budgets"]["context_tokens"] == 65536
    assert manifest["controller"]["patch_extraction"].startswith("container")
    assert manifest["controller"]["mini"]["version"] == "2.4.6"


def test_checked_in_manifest_has_no_hidden_grading_fields_and_balanced_orders():
    manifest = load()
    text = MANIFEST.read_text(encoding="utf-8")
    for hidden in ("gold.patch", "test.patch", "hints.md", '"patch"', '"test_patch"', '"hints_text"'):
        assert hidden not in text
    orders = Counter(tuple(row["order"]) for row in manifest["instances"])
    assert len(orders) == 6
    assert set(orders.values()) == {3, 3, 3, 3, 4, 4}
    for order in orders:
        assert sorted(order) == sorted(protocol.CONDITIONS)
    assert manifest["grading"]["p2p_deltas"]["django__django-10097"]["policy"] == \
        "grading uses the frozen dataset PASS_TO_PASS"


def test_manifest_task_inputs_hashes_are_present_without_statements():
    manifest = load()
    for row in manifest["instances"]:
        assert "problem_statement" not in row
        assert len(row["problem_statement_sha256"]) == 64
        int(row["problem_statement_sha256"], 16)
