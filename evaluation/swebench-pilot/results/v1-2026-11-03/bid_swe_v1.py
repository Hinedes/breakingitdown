#!/usr/bin/env python3
"""BID v1 SWE-bench pilot launcher (all 60 cells, fresh run directory).

Frozen source 7112ff6f3c57, sealed manifest, 20 gold/base-gated instances, fixed Qwen3-4B Q8 server.
Pauses (does not retry) on any infrastructure fault; safe to re-run to resume: completed
checkpoints are skipped by pilot.run_one.

    python3 bid_swe_v1.py --preflight-only
    python3 bid_swe_v1.py
"""

import argparse
import json
import os
import pathlib
import shutil
import signal
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone

BASE = pathlib.Path("/home/iclab/bid-benchmarks/swebench-verified20-2026-11-02")
SRC = BASE / "bid-source-7112ff6f3c57"
PILOT_DIR = SRC / "evaluation" / "swebench-pilot"
MANIFEST = BASE / "manifest-7112ff6f3c57.json"
PUBLIC = BASE / "frozen" / "task-inputs.jsonl"
GRADING = BASE / "frozen" / "grading" / "instances.jsonl"
GATES = BASE / "preflight" / "gates-0e59d6ea.jsonl"
RUNS = BASE / "runs-v1"
RUNTIME = BASE / "runtime" / "v1-2026-11-03"
CANARY_RUNTIME = BASE / "runtime" / "canary-2026-11-02"

SOURCE_SHA = "7112ff6f3c57ce76225902b41526237f7a46d34f6ce4f67e6f143d8f3ff0e5d1"
MANIFEST_SHA = "548464f7d9beaaa96f8e3f725750e4f441472fa57c36830006a6aedf3cf440a4"
MODEL_SHA = "260b5b5b6ad73e44df81a43ea1f5c11c37007b6bac18eb3cd2016e8667c19662"
SERVER_BINARY = "/home/iclab/bid-llama-runtime/src/llama.cpp/build/bin/llama-server"
SERVER_MODEL = "/home/iclab/bid-models/Qwen_Qwen3-4B-Instruct-2507-Q8_0.gguf"
MODEL_ALIAS = "Qwen3-4B-Instruct-2507-Q8_0"
MODEL_UPSTREAM = "http://127.0.0.1:8081/v1/chat/completions"
PYTHON = str(BASE / "venv" / "bin" / "python")
MINI_PYTHON = str(BASE / "mini-venv" / "bin" / "python")
REPO_CACHE = "/home/iclab/.run/repo-mirrors"
GRADE_TIMEOUT = 1800
MIN_FREE_GB = 10.0
OUTER_BUDGET_SECONDS = 48 * 3600
DERIVED = []
ENV = {
    "PATH": "/usr/local/bin:/usr/bin:/bin",
    "HOME": "/home/iclab",
    "PYTHONPATH": str(SRC),
    "PYTHONDONTWRITEBYTECODE": "1",
    "PYTHONUTF8": "1",
    "DOCKER_HOST": "unix:///home/iclab/.run/docker.sock",
    "XDG_RUNTIME_DIR": "/home/iclab/.run",
}

sys.path.insert(0, str(PILOT_DIR))
pilot = None


def load_pilot():
    global pilot
    if pilot is None:
        import pilot as deployed
        pilot = deployed
    return pilot


class Refusal(Exception):
    pass


def utcnow():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_file(path):
    import hashlib
    h = hashlib.sha256()
    with pathlib.Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def apply_env():
    os.environ.clear()
    os.environ.update(ENV)


def sh(cmd, timeout=60):
    p = subprocess.run(cmd, shell=True, capture_output=True, text=True,
                       timeout=timeout, env=os.environ)
    return (p.stdout or "").strip(), (p.stderr or "").strip(), p.returncode


def plan_requirement(existing_keys):
    unexpected = sorted(k for k in existing_keys if "--" not in k)
    return {"present": len(existing_keys), "missing": 60 - len(existing_keys),
            "unexpected": unexpected, "ok": not unexpected}


def preflight_gates():
    if not GATES.is_file():
        raise Refusal(f"gate record missing: {GATES}")
    rows = [json.loads(line) for line in GATES.read_text().splitlines() if line.strip()]
    if len(rows) != 40:
        raise Refusal("gate record is not 40 runs")
    bad = [r for r in rows if r.get("rc") != 0
           or any((r.get("results") or {}).get(k) for k in
                  ("infra_failure_instances", "error_instances"))]
    if bad:
        raise Refusal(f"gate record has faults: {[r['run_id'] for r in bad][:2]}")
    gold = [r for r in rows if r.get("kind") == "gold"]
    base = [r for r in rows if r.get("kind") == "base"]
    if sum(1 for r in gold if (r.get("report") or {}).get("resolved") is True) != 20:
        raise Refusal("gold gate not 20/20")
    base_resolved = [r["instance_id"] for r in base if (r.get("report") or {}).get("resolved") is True]
    if base_resolved != ["django__django-10097"]:
        raise Refusal(f"unexpected base-resolved set: {base_resolved}")
    return {"runs": len(rows), "sha256": sha256_file(GATES)}


def preflight_derived():
    RUNS.mkdir(parents=True, exist_ok=True)
    existing = {p.name for p in RUNS.iterdir() if p.is_dir() and (p / "complete.json").exists()}
    plan = plan_requirement(existing)
    if not plan["ok"]:
        raise Refusal(f"run directory has unexpected entries: {plan}")
    return [], plan


def check_live_state(strict):
    issues = []
    info = {}
    ps, _, _ = sh("ps -ef | grep -E '[l]lama-server|[s]webench-pilot|[p]roxy.py'")
    info["active_processes"] = ps
    if ps:
        issues.append("active llm/pilot/proxy process")
    containers, _, _ = sh("docker ps -a --format '{{.Names}}'")
    info["containers"] = containers
    if containers:
        issues.append(f"docker containers present: {containers.splitlines()[:3]}")
    version, derr, drc = sh("docker info --format '{{.ServerVersion}}'")
    info["docker_server"] = version
    if drc != 0 or not version:
        issues.append(f"docker inaccessible: {derr[:120]}")
    port, _, _ = sh("ss -ltn 2>/dev/null | grep ':8081 ' || true")
    info["port_8081"] = port
    if port:
        issues.append("port 8081 already listening")
    free = shutil.disk_usage(str(BASE)).free / (1024 ** 3)
    info["disk_free_gb"] = round(free, 2)
    if free < MIN_FREE_GB:
        issues.append(f"disk free {free:.1f} GiB")
    if strict and issues:
        raise Refusal("live-state check failed: " + "; ".join(issues))
    return {"ok": not issues, "issues": issues, "info": info}


PROXY_429_ALLOWLIST = (
    "paired-condition token budget exhausted",
    "paired-condition wall budget exceeded",
    "prompt exceeds the",
)


def _prediction_patch(cell_dir):
    path = pathlib.Path(cell_dir) / "controller" / "prediction.jsonl"
    try:
        import json as _json
        return _json.loads(path.read_text().splitlines()[0]).get("model_patch") or ""
    except (OSError, ValueError, IndexError):
        return ""


def _grade_text(cell_dir):
    try:
        return (pathlib.Path(cell_dir) / "grade.log").read_text(errors="replace")
    except OSError:
        return ""


def infra_reason(outcome, cell_dir):
    """Infrastructure reason, or None for an ordinary cell outcome."""
    if outcome.get("status") == "skipped_complete":
        return None
    if outcome.get("grade_returncode") != 0:
        return f"grader returncode={outcome.get('grade_returncode')} timed_out={outcome.get('grade_timed_out')}"
    if outcome.get("grade_timed_out"):
        return "grader timed out"
    grade = outcome.get("grade") or {}
    completed = grade.get("completed_instances")
    infra_count = grade.get("infra_failure_instances")
    errors = grade.get("error_instances")
    if completed is None or infra_count is None or errors is None:
        return "grader counts missing"
    known_apply = False
    if completed == 0 and errors == 1 and "Patch Apply Failed" in _grade_text(cell_dir):
        patch = _prediction_patch(cell_dir)
        if "__pycache__" in patch or ".pyc" in patch:
            return "official apply failure still includes ignored bytecode (export regression)"
        known_apply = True
    if infra_count != 0:
        return f"infra_failure_instances={infra_count}"
    if errors != 0 and not known_apply:
        return f"error_instances={errors}"
    if completed != 1 and not known_apply:
        return f"grader completed_instances={completed}"
    if outcome.get("completed") is not True:
        return f"checkpoint not completed (completed={outcome.get('completed')})"
    if not (pathlib.Path(cell_dir) / "controller" / "prediction.jsonl").exists():
        return "no prediction produced (environment or harness fault)"
    proxy_path = pathlib.Path(cell_dir) / "controller" / "proxy.jsonl"
    if proxy_path.is_file():
        for line in proxy_path.read_text(errors="replace").splitlines():
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            status = rec.get("status")
            if isinstance(status, int) and status >= 500:
                return f"model/proxy HTTP {status}"
            if status == 429:
                err = (rec.get("error") or "").strip()
                if not any(err.startswith(prefix) for prefix in PROXY_429_ALLOWLIST):
                    return f"unexpected 429: {err[:160]}"
            if status == 200 and rec.get("usage_known") is False and not rec.get("error"):
                return "accepted response with unknown usage"
    return None


def stop_server(proc, args):
    pilot_stop = pilot._stop_owned_server(
        args.server_pid_file, args.server_identity_file, args.server_binary,
        args.server_model, args.server_host, args.server_port)
    returncode = None
    escalated = False
    try:
        returncode = proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        escalated = True
        try:
            proc.terminate()
        except ProcessLookupError:
            pass
        try:
            returncode = proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            returncode = None
    alive = proc.poll() is None
    return {"stopped": bool(pilot_stop.get("stopped")) or (returncode is not None and not alive),
            "pid": proc.pid, "returncode": returncode, "pilot_stop": pilot_stop,
            "wrapper_escalated": escalated, "alive_after_stop": alive, "at": utcnow()}


def start_server(args, holder):
    launch = json.loads((CANARY_RUNTIME / "launch.json").read_text())
    cmd = list(launch["command"])
    if launch.get("model_sha256") != MODEL_SHA:
        raise Refusal("launch recipe model hash mismatch")
    if sha256_file(SERVER_MODEL) != MODEL_SHA:
        raise Refusal("model file hash mismatch")
    log = (RUNTIME / "server.log").open("wb")
    proc = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT, env=os.environ)
    holder["proc"] = proc
    (RUNTIME / "server.pid").write_text(f"{proc.pid}\n", encoding="utf-8")
    identity = {"pid": proc.pid, "starttime_ticks": pilot._proc_starttime(proc.pid)}
    (RUNTIME / "server.identity.json").write_text(json.dumps(identity) + "\n", encoding="utf-8")
    deadline = time.monotonic() + 180
    props = None
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise Refusal(f"llama-server exited during startup ({proc.returncode})")
        try:
            with urllib.request.urlopen("http://127.0.0.1:8081/props", timeout=3) as resp:
                props = json.loads(resp.read().decode("utf-8"))
            break
        except Exception:
            time.sleep(2)
    if props is None:
        raise Refusal("llama-server did not answer /props in 180s")
    checks = {"model_path": props.get("model_path"), "model_alias": props.get("model_alias"),
              "total_slots": props.get("total_slots"),
              "n_ctx": (props.get("default_generation_settings") or {}).get("n_ctx")}
    if checks["model_path"] != SERVER_MODEL or checks["model_alias"] != MODEL_ALIAS:
        raise Refusal(f"server props mismatch: {checks}")
    if checks["total_slots"] != 1 or checks["n_ctx"] != 65536:
        raise Refusal(f"server slot/context mismatch: {checks}")
    (RUNTIME / "server-props.json").write_text(json.dumps(props) + "\n", encoding="utf-8")
    return proc, identity, checks


def make_args():
    return argparse.Namespace(
        manifest=str(MANIFEST), public=str(PUBLIC), grading_rows=str(GRADING),
        output=str(RUNS), controller_root=str(BASE), source_root=str(SRC),
        repo_cache=REPO_CACHE, model_upstream=MODEL_UPSTREAM, model=MODEL_ALIAS,
        python=PYTHON, mini_python=MINI_PYTHON, grade_timeout=GRADE_TIMEOUT,
        min_free_gb=MIN_FREE_GB, dry_run=False, execute=True,
        instance=None, condition=None, limit=None,
        server_pid_file=str(RUNTIME / "server.pid"),
        server_identity_file=str(RUNTIME / "server.identity.json"),
        server_binary=SERVER_BINARY, server_model=SERVER_MODEL,
        server_host="127.0.0.1", server_port=8081,
    )


def run_continuation(opts):
    RUNTIME.mkdir(parents=True, exist_ok=True)
    live = check_live_state(strict=not opts.preflight_only)
    manifest = pilot.load_manifest(str(MANIFEST))
    if manifest["manifest_sha256"] != MANIFEST_SHA:
        raise Refusal("manifest self-hash mismatch")
    if manifest["pins"]["bid_source_sha256"] != SOURCE_SHA:
        raise Refusal("manifest source pin mismatch")
    public = pilot.load_public_rows(str(PUBLIC), manifest)
    pilot.verify_provenance(manifest, public, str(GRADING), str(SRC))
    queue = pilot.build_queue(manifest)
    if len(queue) != 60:
        raise Refusal("queue is not 60 cells")
    if opts.limit:
        queue = queue[:opts.limit]
    gates = preflight_gates()
    derived, plan = preflight_derived()
    report = {"at": utcnow(), "source": SOURCE_SHA, "manifest": MANIFEST_SHA,
              "plan": plan, "derived": derived, "gates": gates, "live": live}
    (RUNTIME / "preflight.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print("PREFLIGHT_OK " + json.dumps({"source": SOURCE_SHA, "manifest": MANIFEST_SHA,
                                        "present": plan["present"], "missing": plan["missing"],
                                        "live": live["ok"]}), flush=True)
    if opts.preflight_only:
        return 0

    (RUNTIME / f"launched-{utcnow()}.json").write_text(json.dumps({"at": utcnow(), "pid": os.getpid()}) + "\n",
                                                       encoding="utf-8")
    args = make_args()
    grading = grading_rows_map(GRADING)
    stop_flag = {"requested": False}

    def request_stop(signum, frame):
        stop_flag["requested"] = True

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)

    holder = {}
    paused = None
    fatal = None
    ran = 0
    skipped = 0
    started = time.monotonic()
    notes = []
    try:
        proc, identity, checks = start_server(args, holder)
        report["server"] = {"pid": proc.pid, "identity": identity, "props": checks}
        print(f"SERVER_READY pid={proc.pid} props={checks}", flush=True)

        for index, cell in enumerate(queue, 1):
            if stop_flag["requested"]:
                paused = "signal received"
                break
            if time.monotonic() - started > OUTER_BUDGET_SECONDS:
                paused = "outer budget exhausted"
                break
            try:
                outcome = pilot.run_one(args, manifest, public, grading, cell, index, len(queue))
            except Exception as exc:  # noqa: BLE001
                paused = f"{cell['key']}: launcher exception {exc!r}"
                print(f"PAUSE {paused}", flush=True)
                break
            if outcome.get("status") == "skipped_complete":
                skipped += 1
                print(f"[{index}/60] {cell['key']} SKIP derived checkpoint", flush=True)
                continue
            ran += 1
            cell_dir = RUNS / cell["key"]
            grade = outcome.get("grade") or {}
            if grade.get("completed_instances") == 0 and (grade.get("error_instances") or 0) == 1 \
                    and "Patch Apply Failed" in _grade_text(cell_dir):
                notes.append({"key": cell["key"], "at": utcnow(),
                              "official_patch_apply_failure": True,
                              "run_id": outcome.get("run_id")})
            reason = infra_reason(outcome, cell_dir)
            print(f"[{index}/60] {cell['key']} {outcome.get('cell_status')} "
                  f"grade_rc={outcome.get('grade_returncode')} completed={outcome.get('completed')}"
                  + (f" PAUSE={reason}" if reason else ""), flush=True)
            if reason:
                paused = f"{cell['key']}: {reason}"
                break
    except Refusal as exc:
        fatal = f"startup refused: {exc}"
    except Exception as exc:  # noqa: BLE001
        fatal = f"fatal: {exc!r}"
    finally:
        proc = holder.get("proc")
        if proc is not None:
            try:
                stop = stop_server(proc, args)
            except Exception as exc:  # noqa: BLE001
                stop = {"stopped": False, "error": repr(exc)}
            report["server_stop"] = stop
            (RUNTIME / "stop.json").write_text(json.dumps(stop, indent=2) + "\n", encoding="utf-8")
            (RUNS / "server-stopped.json").write_text(json.dumps(stop, sort_keys=True) + "\n",
                                                      encoding="utf-8")
        try:
            pilot.write_csv(pilot.summary_rows(str(RUNS), queue), str(RUNS / "outcomes.csv"))
        except Exception as exc:  # noqa: BLE001
            report["csv_error"] = repr(exc)
        (RUNTIME / "notes.jsonl").write_text(
            "".join(json.dumps(n) + "\n" for n in notes), encoding="utf-8")

    checkpoints = [c["key"] for c in queue if (RUNS / c["key"] / "complete.json").exists()]
    report.update({"finished_utc": utcnow(), "ran_cells": ran, "skipped_cells": skipped,
                   "checkpoints_present": len(checkpoints), "paused": paused, "fatal": fatal,
                   "notes": notes})
    if fatal:
        report["status"] = "error"
    elif paused:
        report["status"] = "paused"
    else:
        report["status"] = "complete" if len(checkpoints) == 60 else "incomplete"
    (RUNTIME / "final.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print("FINAL_JSON " + json.dumps({
        "status": report["status"], "ran_cells": ran, "skipped_cells": skipped,
        "checkpoints_present": len(checkpoints), "paused": paused, "fatal": fatal,
        "server_stop": report.get("server_stop"), "runtime": str(RUNTIME)}), flush=True)
    if fatal:
        return 4 if fatal.startswith("startup refused") else 3
    return 0 if report["status"] == "complete" else 3


def grading_rows_map(grading_path):
    rows = [json.loads(line) for line in pathlib.Path(grading_path).read_text().splitlines() if line.strip()]
    return {row["instance_id"]: row for row in rows}


def _self_check():
    import tempfile

    assert plan_requirement(set())["ok"] and plan_requirement({"x"})["ok"] is False
    assert plan_requirement({"django__django-10097--bid"})["missing"] == 59

    def cell(grade, log="", proxy=(), patch="diff --git a/core.py b/core.py\n"):
        d = pathlib.Path(tempfile.mkdtemp())
        (d / "controller").mkdir()
        (d / "controller" / "prediction.jsonl").write_text(
            json.dumps({"instance_id": "x", "model_name_or_path": "m", "model_patch": patch}) + "\n")
        (d / "grade.log").write_text(log)
        if proxy:
            (d / "controller" / "proxy.jsonl").write_text(
                "".join(json.dumps(r) + "\n" for r in proxy))
        outcome = {"status": "error", "grade_returncode": 0, "grade_timed_out": False,
                   "completed": True, "grade": grade}
        return outcome, d

    base = {"completed_instances": 0, "infra_failure_instances": 0, "error_instances": 1}
    apply_log = ">>>>> Patch Apply Failed:\nThe next patch would create ..."
    o, d = cell(base, log=apply_log)
    assert infra_reason(o, d) is None, "known clean apply failure must be ordinary"

    o, d = cell(base, log=apply_log, proxy=[{"status": 502, "error": "bad gateway"}])
    assert infra_reason(o, d) is not None, "apply failure + HTTP 502 must be infra"

    o, d = cell(dict(base, infra_failure_instances=1), log=apply_log)
    assert infra_reason(o, d) is not None, "apply failure + infra count must be infra"

    o, d = cell(dict(base), log="some other failure")
    assert infra_reason(o, d) is not None, "unknown error 1 must be infra"

    o, d = cell({"completed_instances": 1, "infra_failure_instances": 0, "error_instances": 0})
    assert infra_reason(o, d) is None, "clean cell must be ordinary"
    print("SELF_CHECK_OK supervisor plan + apply-failure classification")
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preflight-only", action="store_true")
    parser.add_argument("--self-check", action="store_true")
    parser.add_argument("--limit", type=int, default=None,
                        help="run only the first N queue cells (canary); cells stay valid run cells")
    opts = parser.parse_args()
    if opts.self_check:
        return _self_check()
    apply_env()
    if not (PILOT_DIR / "pilot.py").is_file():
        raise Refusal("deployed pilot missing")
    load_pilot()
    return run_continuation(opts)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Refusal as exc:
        print(f"REFUSED {exc}", file=sys.stderr, flush=True)
        sys.exit(4)
