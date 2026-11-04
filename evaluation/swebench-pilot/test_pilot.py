import csv
import json
import os
import subprocess
from collections import Counter
from pathlib import Path

import pytest

import pilot


MANIFEST = Path(__file__).with_name("manifest.json")


def test_manifest_validates_and_queue_is_paired():
    manifest = pilot.load_manifest(MANIFEST)
    queue = pilot.build_queue(manifest)
    assert len(queue) == 60
    per_issue = Counter(cell["instance_id"] for cell in queue)
    assert set(per_issue.values()) == {3}
    for iid in per_issue:
        assert sorted(cell["condition"] for cell in queue if cell["instance_id"] == iid) == sorted(("bid", "worker", "mini"))


def test_manifest_tamper_is_rejected(tmp_path):
    data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    data["instances"][0]["instance_id"] = "tampered__repo-1"
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(pilot.PilotError):
        pilot.load_manifest(path)


def test_resume_csv_uses_checkpoints_for_all_60_cells(tmp_path):
    manifest = pilot.load_manifest(MANIFEST)
    queue = pilot.build_queue(manifest)
    prior = {
        "key": queue[0]["key"], "instance_id": queue[0]["instance_id"],
        "condition": queue[0]["condition"], "completed": True, "grade": {"resolved_instances": 1},
    }
    new = {
        "key": queue[1]["key"], "instance_id": queue[1]["instance_id"],
        "condition": queue[1]["condition"], "completed": False, "grade": {},
        "cell_error": "declared failure",
    }
    for row in (prior, new):
        cell = tmp_path / row["key"]
        cell.mkdir(parents=True)
        (cell / "complete.json").write_text(json.dumps(row), encoding="utf-8")
    rows = pilot.summary_rows(tmp_path, queue)
    assert len(rows) == 60
    pilot.write_csv(rows, tmp_path / "outcomes.csv")
    with open(tmp_path / "outcomes.csv", newline="", encoding="utf-8") as handle:
        read = list(csv.DictReader(handle))
    assert len(read) == 60
    by_key = {row["key"]: row for row in read}
    assert by_key[prior["key"]]["completed"] == "True"
    assert by_key[new["key"]]["completed"] == "False"
    assert by_key[new["key"]]["cell_error"] == "declared failure"
    assert by_key[queue[2]["key"]]["checkpoint_missing"] == "True"


def _synthetic_provenance(tmp_path, monkeypatch):
    import protocol
    rows = [
        {"instance_id": f"repo__project-{i}", "repo": "repo/project",
         "base_commit": f"base{i}", "problem_statement": f"issue {i}"}
        for i in range(20)
    ]
    manifest = protocol.build_manifest(rows, created_at="2026-01-01T00:00:00Z",
                                       bid_source_sha256="expected",
                                       bid_source_manifest="SOURCE_FINGERPRINTS.sha256")
    public_path = tmp_path / "public.jsonl"
    public_path.write_text("\n".join(protocol.canonical_json(r) for r in rows) + "\n", encoding="utf-8")
    grading_path = tmp_path / "grading.jsonl"
    grading_path.write_text('{"instance_id":"hidden"}\n', encoding="utf-8")
    manifest["grading"] = {"enriched_rows_sha256": pilot._sha256_bytes(grading_path.read_bytes())}
    manifest = protocol.seal_manifest(manifest)
    monkeypatch.setattr(pilot, "EXPECTED_IDS_SHA256", manifest["sample"]["ids_sha256"])
    monkeypatch.setattr(pilot, "source_tree_hash", lambda root: "expected")
    return manifest, rows, public_path, grading_path


def test_provenance_rejects_public_hidden_or_source_change(tmp_path, monkeypatch):
    manifest, rows, public_path, grading_path = _synthetic_provenance(tmp_path, monkeypatch)
    public = {r["instance_id"]: r for r in rows}
    pilot.verify_provenance(manifest, public, grading_path, tmp_path)
    changed = dict(public)
    changed[rows[0]["instance_id"]] = dict(rows[0], base_commit="tampered")
    with pytest.raises(pilot.PilotError):
        pilot.verify_provenance(manifest, changed, grading_path, tmp_path)
    grading_path.write_text('{"instance_id":"changed"}\n', encoding="utf-8")
    with pytest.raises(pilot.PilotError):
        pilot.verify_provenance(manifest, public, grading_path, tmp_path)
    grading_path.write_text('{"instance_id":"hidden"}\n', encoding="utf-8")
    monkeypatch.setattr(pilot, "source_tree_hash", lambda root: "changed")
    with pytest.raises(pilot.PilotError):
        pilot.verify_provenance(manifest, public, grading_path, tmp_path)


def test_owned_server_stop_fails_closed_without_identity(tmp_path):
    pid_file = tmp_path / "server.pid"
    pid_file.write_text(str(os.getpid()), encoding="utf-8")
    result = pilot._stop_owned_server(
        pid_file, identity_file=None, binary="/bin/true", model="/tmp/model.gguf",
    )
    assert result["stopped"] is False


@pytest.mark.skipif(os.name == "nt", reason="requires /proc")
def test_owned_server_never_kills_unrelated_pid(tmp_path):
    process = subprocess.Popen(["sleep", "5"])
    try:
        pid_file = tmp_path / "server.pid"
        pid_file.write_text(str(process.pid), encoding="utf-8")
        identity = tmp_path / "identity.json"
        identity.write_text(json.dumps({"pid": process.pid, "starttime_ticks": -1}),
                            encoding="utf-8")
        result = pilot._stop_owned_server(
            pid_file, str(identity), "/bin/sleep", "/tmp/model.gguf",
        )
        assert result["stopped"] is False
        assert process.poll() is None
    finally:
        process.terminate()
        process.wait(timeout=5)
