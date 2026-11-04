"""Minimal controller-side runner for one paired-pilot cell.

This module deliberately contains no grading logic. It prepares a fresh base
checkout, runs one arm, and writes an official-format prediction whose diff
excludes BID control files. Grading is a separate controller step using the
pinned SWE-bench evaluator.

No scored run is started by importing or testing this file.
"""

import argparse
import hashlib
import json
import os
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import protocol


class RunnerError(RuntimeError):
    pass


def _git(args, cwd, check=True):
    result = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, timeout=600
    )
    if check and result.returncode != 0:
        raise RunnerError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result


def clone_checkout(source, base_commit, destination):
    """Fresh, shallow, base-only checkout with no origin/future refs."""
    destination = Path(destination)
    if destination.exists():
        raise RunnerError(f"checkout destination already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    _git(["init", "--quiet", str(destination)], cwd=destination.parent)
    _git(["remote", "add", "origin", str(source)], cwd=destination)
    _git(["fetch", "--quiet", "--depth", "1", "--no-tags", "origin", base_commit], cwd=destination)
    _git(["checkout", "--quiet", "--detach", "FETCH_HEAD"], cwd=destination)
    _git(["remote", "remove", "origin"], cwd=destination)
    for leftover in ("FETCH_HEAD", "ORIG_HEAD", "packed-refs"):
        path = destination / ".git" / leftover
        if path.exists():
            path.unlink()
    shutil.rmtree(destination / ".git" / "logs", ignore_errors=True)
    _git(["reflog", "expire", "--expire=now", "--all"], cwd=destination)
    _git(["gc", "--quiet", "--prune=now"], cwd=destination)
    head = _git(["rev-parse", "HEAD"], cwd=destination).stdout.strip()
    if head != base_commit:
        raise RunnerError(f"checkout is {head}, expected {base_commit}")
    if _git(["for-each-ref", "--format=%(refname)"], cwd=destination).stdout.strip():
        raise RunnerError("shallow checkout unexpectedly contains refs")
    if _git(["status", "--porcelain"], cwd=destination).stdout.strip():
        raise RunnerError("fresh checkout is not clean")
    if os.name == "nt":
        # Git marks object files read-only; BID's backup cleanup needs them writable.
        for dirpath, dirnames, filenames in os.walk(destination / ".git"):
            for name in dirnames:
                os.chmod(os.path.join(dirpath, name), 0o777)
            for name in filenames:
                os.chmod(os.path.join(dirpath, name), 0o666)
    return destination


def public_prompt(row):
    """Only the allowlisted fields; gold/test/hints never enter this string."""
    extra = set(row) - set(protocol.PUBLIC_FIELDS)
    if extra:
        raise RunnerError(f"agent input contains non-public fields: {sorted(extra)}")
    return (
        f"Repository: {row['repo']}\n"
        f"Base commit: {row['base_commit']}\n\n"
        f"{row['problem_statement']}"
    )


def extract_patch(workspace, base_commit):
    """Final repo diff, including new files, then drop BID/controller files."""
    workspace = Path(workspace)
    excludes = [f":(exclude){path.rstrip('/')}" for path in protocol.CONTROL_PATHS]
    excludes.append(f":(exclude){protocol.EMPTY_PATCH_PATH}")
    _git(["add", "-N", "--", ".", *excludes], cwd=workspace)
    diff = _git(["diff", "--binary", base_commit, "--", "."], cwd=workspace).stdout
    return protocol.filter_patch(diff)


def write_predictions(path, rows):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="\n", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
    return path


def prediction_for(instance_id, arm, workspace, base_commit):
    patch = extract_patch(workspace, base_commit)
    return protocol.prediction(instance_id, f"bid-pilot-{arm}", patch)


class BudgetBackend:
    """Charge every model response to the paired-condition budget."""

    def __init__(self, backend, budget=None):
        self.backend = backend
        self.budget = budget or protocol.Budget()

    @property
    def role(self):
        return getattr(self.backend, "role", None)

    @role.setter
    def role(self, value):
        if hasattr(self.backend, "role"):
            self.backend.role = value

    def run(self, messages, tools, max_tokens=None, timeout=None, grammar=None):
        self.budget.check_time()
        if max_tokens is not None and max_tokens > self.budget.request_output_limit:
            raise ValueError("request output cap exceeds the paired-condition limit")
        response = self.backend.run(messages, tools, max_tokens, timeout, grammar)
        usage = response.get("usage") or {}
        self.budget.charge(
            int(usage.get("prompt_tokens", 0)),
            int(usage.get("completion_tokens", 0)),
            int(usage.get("prompt_tokens_details", {}).get("cached_tokens", 0))
            if isinstance(usage.get("prompt_tokens_details"), dict) else 0,
        )
        return response


def bid_config(workspace, endpoint, model_name, seed=None, task_statement=""):
    """BID's full flow, but with the pilot budget and container-local RUN."""
    return {
        "endpoint": endpoint,
        "model_name": model_name,
        "max_tokens": protocol.REQUEST_OUTPUT_LIMIT,
        "workspace": str(workspace),
        "request_timeout": 120,
        "inactivity_timeout": 120,
        "worker_timeout": protocol.WALL_BUDGET_SECONDS,
        "repeat_action_limit": 5,
        "run_timeout": 120,
        "max_searches_per_worker": protocol.MAX_SEARCHES,
        "search_endpoint": "",
        "provisional_batch": protocol.PROVISIONAL_BATCH,
        "max_task_reworks": 3,
        "max_manager_reworks": 3,
        "allow_host_run": False,  # RUN is routed to the task container
        "run_executor": None,
        # MAP/FIND: without them the Worker can only guess paths in a repository it has never seen
        "repo_context_mode": "tools",
        "protocol_grammar": protocol.PROTOCOL_GRAMMAR,
        "task_statement": task_statement,
        "seed": seed,
    }


def assert_no_control_collision(workspace):
    """BID must not overwrite pre-existing repo files with its control docs."""
    workspace = Path(workspace)
    collisions = [
        path for path in protocol.CONTROL_PATHS
        if (workspace / path.rstrip("/")).exists()
    ]
    if collisions:
        raise RunnerError(f"base checkout already contains BID control paths: {collisions}")


def run_bid(workspace, row, config, backend, run_executor=None, extract_patch_fn=None):
    """Full BID Manager/Worker/Task Reviewer/reconciliation flow."""
    from bid import harness

    assert_no_control_collision(workspace)
    config = dict(config, workspace=str(workspace), run_executor=run_executor)
    result = harness.init_project(row["problem_statement"], config, backend)
    if result.get("status") != "success":
        raise RunnerError(f"BID init failed: {result}")
    result = harness.run_project(config, backend)
    if result.get("status") != "done":
        raise RunnerError(f"BID run failed: {result}")
    return extract_patch_fn() if extract_patch_fn else ""


def run_worker(workspace, row, config, backend, run_executor=None, extract_patch_fn=None):
    """Matched single WorkerAdapter: one task, no Manager or Reviewer."""
    from bid import adapter, harness, vc as vc_mod

    assert_no_control_collision(workspace)
    config = dict(config, workspace=str(workspace), run_executor=run_executor)
    harness.ensure_workspace(workspace)
    todo = "- [ ] T1 — Resolve the public issue described in the task statement.\n"
    (Path(workspace) / "docs" / "todo.md").write_text(todo, encoding="utf-8")
    version_control = vc_mod.VersionControl(workspace)
    version_control.init()
    result = adapter.WorkerAdapter(config, 1).run(backend)
    if result.get("status") != "done":
        raise RunnerError(f"worker failed: {result}")
    return extract_patch_fn() if extract_patch_fn else ""


def mini_command(workspace, task, model_name, output, python="python", config=None, environment="docker"):
    """Pinned mini-SWE-agent stock policy; model/endpoint are environment overrides."""
    command = [
        python, "-m", "minisweagent.run.mini",
        "-m", f"openai/{model_name}",
        "-t", task,
        "-c", "mini.yaml",
    ]
    if config:
        command += ["-c", str(config)]
    command += [
        "-y", "--exit-immediately",
        "--environment-class", environment,
        "-o", str(output),
    ]
    return command


def mini_config_text(image, checkout, seed, network="none", cell_id="adhoc"):
    """Per-cell config: stock mini policy plus isolated Docker and frozen sampling."""
    checkout = Path(checkout).resolve()
    return (
        "agent:\n"
        "  mode: yolo\n"
        "  confirm_exit: false\n"
        "environment:\n"
        "  environment_class: docker\n"
        f"  image: {json.dumps(image)}\n"
        "  cwd: /testbed\n"
        "  run_args:\n"
        "    - --rm\n"
        "    - --label\n"
        f"    - {json.dumps('bid-pilot-cell=' + cell_id)}\n"
        "    - --network\n"
        f"    - {json.dumps(network)}\n"
        "    - --cap-drop\n"
        "    - ALL\n"
        "    - --security-opt\n"
        "    - no-new-privileges\n"
        "    - -v\n"
        f"    - {json.dumps(str(checkout) + ':/testbed')}\n"
        "    - -w\n"
        "    - /testbed\n"
        "model:\n"
        "  cost_tracking: ignore_errors\n"
        "  model_kwargs:\n"
        "    temperature: 0.6\n"
        "    top_p: 0.95\n"
        "    max_tokens: 4096\n"
        f"    seed: {int(seed)}\n"
        "    presence_penalty: 0.0\n"
        "    drop_params: true\n"
        "    extra_body:\n"
        "      top_k: 20\n"
        "      min_p: 0.0\n"
        "      repetition_penalty: 1.0\n"
        "      chat_template_kwargs:\n"
        "        enable_thinking: true\n"
    )


def per_run_executor(image, project_dir, cell_id, env=None):
    """One short-lived offline container per RUN; project edits persist.

    The container's PID 1 is the command; when it exits, Docker kills any
    background descendants. On timeout or success the container is removed
    before host bookkeeping resumes. Outside-project state is ephemeral.
    """
    project_dir = Path(project_dir).resolve()
    container_env = env or os.environ
    def execute(argv, cwd=None, timeout=None):
        argv = list(argv)
        if argv and argv[0] == sys.executable:
            argv[0] = "python"
        if argv and argv[0].startswith("/") and "python" in os.path.basename(argv[0]):
            argv[0] = "python"
        name = f"bid-run-{uuid.uuid4().hex[:12]}"
        command = [
            "docker", "run", "--rm", "--name", name,
            "--label", f"bid-pilot-run={cell_id}",
            "--network", "none", "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges",
            "--user", "root",
            "-v", f"{project_dir}:/testbed", "-w", "/testbed",
            image, "sh", "-lc", 'exec "$@"', "sh", *argv,
        ]
        try:
            return subprocess.run(
                command, capture_output=True, text=True, timeout=timeout,
                env=container_env, check=False,
            )
        except subprocess.TimeoutExpired:
            subprocess.run(["docker", "rm", "-f", name], capture_output=True,
                           text=True, timeout=60, env=container_env)
            raise
        finally:
            subprocess.run(["docker", "rm", "-f", name], capture_output=True,
                           text=True, timeout=60, env=container_env)
    return execute


def extract_patch_in_container(image, checkout, base_commit, timeout=120, env=None):
    """Run untrusted git diff inside a no-network, no-capability container.

    The controller must not run host git against a model-modifiable .git.
    """
    checkout = Path(checkout).resolve()
    run_dir = checkout.parent
    excludes = [f":(exclude){path.rstrip('/')}" for path in protocol.CONTROL_PATHS]
    excludes.append(f":(exclude){protocol.EMPTY_PATCH_PATH}")
    pathspec = " ".join(shlex.quote(item) for item in (".", *excludes))
    script = (
        "git -c safe.directory='*' -c core.fileMode=false add -N -- "
        f"{pathspec} && "
        f"git -c safe.directory='*' -c core.fileMode=false diff --binary {base_commit} -- ."
    )
    command = [
        "docker", "run", "--rm", "--network", "none",
        "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
        "--read-only", "--tmpfs", "/tmp:rw,noexec,nosuid",
        "--label", f"bid-pilot-extract={uuid.uuid4().hex[:10]}",
        "-e", "HOME=/tmp",
        "-v", f"{run_dir}:/work",
        "-w", f"/work/{checkout.name}",
        image, "sh", "-lc", script,
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=timeout, env=env)
    if result.returncode != 0:
        raise RunnerError(f"container patch extraction failed: {result.stderr.strip()[:1000]}")
    return protocol.filter_patch(result.stdout)


def cleanup_cell(label, env=None):
    """Remove every container carrying a cell label, including mini."""
    if not label:
        return []
    listing = subprocess.run(
        ["docker", "ps", "-aq", "--filter", f"label={label}"],
        capture_output=True, text=True, timeout=60, env=env,
    )
    ids = [item for item in listing.stdout.split() if item]
    if ids:
        subprocess.run(["docker", "rm", "-f", *ids], capture_output=True, text=True, timeout=120, env=env)
    return ids


def _read_deadline(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def run_with_watchdog(command, deadline_file, cleanup, *, cwd=None, env=None,
                      setup_grace=120, poll=0.2):
    """Run a child with one shared proxy deadline and kill its whole cell.

    The proxy writes the monotonic deadline at the first model request. Until
    then only setup is allowed (bounded by setup_grace). After the anchor the
    child, every per-RUN container, and the mini container are killed at the
    deadline; no tool work gets extra time.
    """
    deadline_file = Path(deadline_file)
    process = subprocess.Popen(
        command, cwd=cwd, env=env, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, text=True, start_new_session=True,
    )
    setup_started = time.monotonic()
    state = {"timed_out": False, "setup_seconds": None, "anchor": None, "deadline": None}
    done = threading.Event()

    def watchdog():
        try:
            while process.poll() is None and not done.wait(poll):
                now = time.monotonic()
                record = _read_deadline(deadline_file)
                if record and state["anchor"] is None:
                    state["anchor"] = float(record["started_monotonic"])
                    state["deadline"] = float(record["deadline_monotonic"])
                    state["setup_seconds"] = max(0.0, state["anchor"] - setup_started)
                if state["deadline"] is not None:
                    if now >= state["deadline"]:
                        state["timed_out"] = True
                        break
                elif now - setup_started > setup_grace:
                    state["timed_out"] = True
                    break
            if process.poll() is None:
                try:
                    if os.name == "nt":
                        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                                       capture_output=True, check=False)
                    else:
                        os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                except ProcessLookupError:
                    pass
                try:
                    cleanup()
                except Exception:
                    pass
        finally:
            done.set()

    thread = threading.Thread(target=watchdog, daemon=True)
    thread.start()
    output, _ = process.communicate()
    done.set()
    thread.join(timeout=2)
    if state["anchor"] is None:
        record = _read_deadline(deadline_file)
        if record:
            state["anchor"] = float(record["started_monotonic"])
            state["deadline"] = float(record["deadline_monotonic"])
            state["setup_seconds"] = max(0.0, state["anchor"] - setup_started)
    return {
        "returncode": process.returncode,
        "timed_out": state["timed_out"],
        "output": output,
        "setup_seconds": state["setup_seconds"],
        "anchor_monotonic": state["anchor"],
        "deadline_monotonic": state["deadline"],
    }


def _pick_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _wait_port(port, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                return True
        except OSError:
            time.sleep(0.1)
    return False


def start_proxy(upstream, seconds, tokens, seed, model, log_path, cell, role, python,
                deadline_file=None, trace_path=None):
    """Start the controller proxy after cell setup; return its base /v1 URL."""
    port = _pick_port()
    log_path = Path(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log = open(log_path, "ab")
    command = [
        python, str(Path(__file__).with_name("proxy.py")),
        "--listen", f"127.0.0.1:{port}",
        "--upstream", upstream,
        "--seconds", str(int(seconds)),
        "--tokens", str(int(tokens)),
        "--context", str(protocol.CONTEXT_TOKENS),
        "--seed", str(int(seed)),
        "--model", model,
        "--log", str(log_path),
        "--cell", str(cell),
        "--role", str(role),
    ]
    if deadline_file:
        command += ["--deadline-file", str(deadline_file)]
    if trace_path:
        command += ["--trace", str(trace_path)]
    process = subprocess.Popen(
        command, stdout=log, stderr=subprocess.STDOUT,
        start_new_session=True, env=os.environ,
    )
    if not _wait_port(port):
        process.kill()
        raise RunnerError("model proxy failed to start")
    return process, f"http://127.0.0.1:{port}/v1"


def stop_proxy(process):
    if process is None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()


def run_agent_cell(arm, cell_dir, row, repo_source, image, model_upstream, model_name,
                   seed, python, timeout=protocol.WALL_BUDGET_SECONDS):
    """One BID/Worker cell: host policy, per-RUN offline containers, shared watchdog."""
    cell_dir = Path(cell_dir)
    workspace = cell_dir / "workspace" / "repo"
    controller_dir = cell_dir / "controller"
    controller_dir.mkdir(parents=True, exist_ok=True)
    setup_started = time.monotonic()
    clone_checkout(repo_source, row["base_commit"], workspace)
    task_path = controller_dir / "task.json"
    task_path.write_text(json.dumps(protocol.public_task(row), sort_keys=True) + "\n", encoding="utf-8")
    cell_id = f"{arm}-{uuid.uuid4().hex[:10]}"
    deadline_file = controller_dir / "deadline.json"
    deadline_file.unlink(missing_ok=True)
    env = dict(os.environ)
    root = str(Path(__file__).resolve().parents[2])
    env["PYTHONPATH"] = root + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    proxy = None
    run_result = {"returncode": -1, "timed_out": False, "output": "",
                  "setup_seconds": None, "anchor_monotonic": None, "deadline_monotonic": None}
    terminal = "error"
    error = ""
    try:
        inspect = subprocess.run(["docker", "image", "inspect", image],
                                 capture_output=True, text=True, timeout=60, env=env)
        if inspect.returncode != 0:
            raise RunnerError(f"task image missing: {image}")
        proxy, model_base = start_proxy(
            model_upstream, timeout, protocol.TOKEN_BUDGET, seed, model_name,
            controller_dir / "proxy.jsonl", cell_id, arm, python,
            deadline_file=deadline_file, trace_path=controller_dir / "trace.jsonl",
        )
        command = [
            python, str(Path(__file__)), "--arm", arm,
            "--task-json", str(task_path), "--workspace", str(workspace),
            "--endpoint", model_base + "/chat/completions",
            "--model", model_name, "--seed", str(int(seed)),
            "--output", str(controller_dir / "prediction.jsonl"),
            "--executor-image", image, "--executor-project", str(workspace),
            "--executor-cell", cell_id,
        ]
        run_result = run_with_watchdog(
            command, deadline_file,
            cleanup=lambda: cleanup_cell(f"bid-pilot-run={cell_id}", env=env),
            cwd=str(cell_dir), env=env, setup_grace=120,
        )
        if run_result.get("anchor_monotonic") is not None:
            run_result["setup_seconds"] = max(
                0.0, run_result["anchor_monotonic"] - setup_started
            )
        (controller_dir / "runner.log").write_text(run_result.get("output") or "", encoding="utf-8")
        inner = _read_inner_status(controller_dir / "status.json")
        terminal = "ok" if run_result["returncode"] == 0 and not run_result["timed_out"] else "error"
        error = "" if terminal == "ok" else f"runner exit={run_result['returncode']} timed_out={run_result['timed_out']}"
        if inner and inner.get("terminal_status") == "error":
            terminal = "error"
            error = inner.get("error") or "inner harness declared error"
    except Exception as exc:
        terminal, error = "error", str(exc)
    finally:
        stop_proxy(proxy)
        cleanup_cell(f"bid-pilot-run={cell_id}", env=env)
    try:
        patch = extract_patch_in_container(image, workspace, row["base_commit"], env=env)
    except Exception as exc:
        patch = ""
        terminal = "error"
        error = (error + f"; trusted patch extraction failed: {exc}").strip("; ")
    predicted = protocol.prediction(row["instance_id"], f"bid-pilot-{arm}", patch)
    write_predictions(controller_dir / "prediction.jsonl", [predicted])
    status = write_status(controller_dir / "status.json", status=terminal, error=error, patch=patch)
    status.update({
        "arm": arm,
        "runner_returncode": run_result["returncode"],
        "runner_timed_out": run_result["timed_out"],
        "setup_seconds": run_result.get("setup_seconds"),
        "anchor_monotonic": run_result.get("anchor_monotonic"),
        "deadline_monotonic": run_result.get("deadline_monotonic"),
        "executor": "per-RUN offline container, project only at /testbed",
    })
    (controller_dir / "status.json").write_text(json.dumps(status, sort_keys=True) + "\n", encoding="utf-8")
    return {"status": terminal, "error": error, "prediction": predicted, "status_record": status}


def run_mini_cell(workspace, row, image, model_name, model_base, python, output_dir, seed,
                  timeout=600, cleanup=None, network="none", cell_id="adhoc",
                  extract_patch_fn=None, deadline_file=None):
    """Controller-side mini arm; mini starts its own Docker environment."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    config_path = output_dir / "mini_pilot.yaml"
    config_path.write_text(mini_config_text(image, workspace, seed, network, cell_id), encoding="utf-8")
    trajectory = output_dir / "trajectory.json"
    prediction_path = output_dir / "prediction.jsonl"
    status_path = output_dir / "status.json"
    write_predictions(prediction_path,
                      [protocol.prediction(row["instance_id"], "bid-pilot-mini", "")])
    write_status(status_path, status="running")
    command = mini_command(workspace, row["problem_statement"], model_name, trajectory,
                           python=python, config=config_path)
    env = dict(os.environ)
    env["OPENAI_API_BASE"] = model_base
    env["OPENAI_API_KEY"] = "controller-only"
    env["MSWEA_CONFIGURED"] = "true"
    env["MSWEA_GLOBAL_CONFIG_DIR"] = str(output_dir / "mini-global")
    deadline_file = Path(deadline_file) if deadline_file else output_dir / "deadline.json"
    result = run_with_watchdog(
        command, deadline_file,
        cleanup=cleanup or (lambda: None),
        cwd=workspace, env=env, setup_grace=120,
    )
    (output_dir / "mini.log").write_text(result.get("output") or "", encoding="utf-8")
    terminal = "ok" if result["returncode"] == 0 and not result["timed_out"] else "error"
    error = "" if terminal == "ok" else f"mini exit={result['returncode']} timed_out={result['timed_out']}"
    if cleanup:
        cleanup()
    patch = ""
    try:
        patch = (extract_patch_fn or (lambda: extract_patch(workspace, row["base_commit"])))()
    except Exception as exc:
        error = (error + f"; patch extraction failed: {exc}").strip("; ")
        terminal = "error"
    predicted = protocol.prediction(row["instance_id"], "bid-pilot-mini", patch)
    write_predictions(prediction_path, [predicted])
    status = write_status(status_path, status=terminal, error=error, patch=patch)
    status.update({
        "arm": "mini",
        "returncode": result["returncode"],
        "timed_out": result["timed_out"],
        "setup_seconds": result.get("setup_seconds"),
        "anchor_monotonic": result.get("anchor_monotonic"),
        "deadline_monotonic": result.get("deadline_monotonic"),
    })
    status_path.write_text(json.dumps(status, sort_keys=True) + "\n", encoding="utf-8")
    return {
        "status": terminal,
        "returncode": result["returncode"],
        "timed_out": result["timed_out"],
        "error": error,
        "prediction": predicted,
        "setup_seconds": result.get("setup_seconds"),
        "anchor_monotonic": result.get("anchor_monotonic"),
        "deadline_monotonic": result.get("deadline_monotonic"),
    }


def run_mini_agent_cell(cell_dir, row, repo_source, image, model_upstream, model_name,
                        seed, python, mini_python, timeout=protocol.WALL_BUDGET_SECONDS):
    """One mini cell: host mini process, offline task container, trusted extraction."""
    cell_dir = Path(cell_dir)
    workspace = cell_dir / "workspace" / "repo"
    controller_dir = cell_dir / "controller"
    controller_dir.mkdir(parents=True, exist_ok=True)
    setup_started = time.monotonic()
    clone_checkout(repo_source, row["base_commit"], workspace)
    env = dict(os.environ)
    inspect = subprocess.run(["docker", "image", "inspect", image],
                             capture_output=True, text=True, timeout=60, env=env)
    if inspect.returncode != 0:
        raise RunnerError(f"mini image missing: {image}")
    cell_id = f"mini-{uuid.uuid4().hex[:10]}"
    deadline_file = controller_dir / "deadline.json"
    deadline_file.unlink(missing_ok=True)
    proxy = None
    result = {"status": "error", "error": "mini did not run", "prediction": None}
    try:
        proxy, model_base = start_proxy(
            model_upstream, timeout, protocol.TOKEN_BUDGET, seed, model_name,
            controller_dir / "proxy.jsonl", cell_id, "mini", python,
            deadline_file=deadline_file, trace_path=controller_dir / "trace.jsonl",
        )
        result = run_mini_cell(
            workspace, row, image, model_name, model_base, mini_python,
            controller_dir, seed, timeout=timeout,
            cleanup=lambda: cleanup_cell(f"bid-pilot-cell={cell_id}", env=env),
            network="none", cell_id=cell_id,
            extract_patch_fn=lambda: extract_patch_in_container(image, workspace, row["base_commit"], env=env),
            deadline_file=deadline_file,
        )
        if result.get("anchor_monotonic") is not None:
            result["setup_seconds"] = max(
                0.0, result["anchor_monotonic"] - setup_started
            )
            status_path = controller_dir / "status.json"
            if status_path.exists():
                status_record = json.loads(status_path.read_text(encoding="utf-8"))
                status_record["setup_seconds"] = result["setup_seconds"]
                status_path.write_text(json.dumps(status_record, sort_keys=True) + "\n",
                                       encoding="utf-8")
    finally:
        stop_proxy(proxy)
        cleanup_cell(f"bid-pilot-cell={cell_id}", env=env)
    return result


def load_task(path):
    row = json.loads(Path(path).read_text(encoding="utf-8"))
    extra = set(row) - set(protocol.PUBLIC_FIELDS)
    if extra:
        raise RunnerError(f"task file contains non-public fields: {sorted(extra)}")
    return row


def _read_inner_status(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def write_status(path, *, status, error="", patch=""):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "terminal_status": status,
        "error": error,
        "patch_empty": not bool(patch),
        "patch_sha256": hashlib.sha256(patch.encode()).hexdigest(),
        "timestamp": time.time(),
    }
    path.write_text(json.dumps(record, sort_keys=True) + "\n", encoding="utf-8")
    return record


def run_cell(arm, workspace, row, endpoint, model_name, seed, output, run_executor=None,
             extract_patch_fn=None, defer_extract=True):
    """Host-side arm runner; RUN is routed to the task container.

    Production controllers set ``defer_extract=True`` so host git never touches
    the model-modifiable tree; the controller extracts in a container.
    """
    if not defer_extract and extract_patch_fn is None:
        extract_patch_fn = lambda: extract_patch(workspace, row["base_commit"])
    if arm not in ("bid", "worker"):
        raise RunnerError("mini runs through its stock Docker environment, not this entry point")
    output = Path(output)
    status_path = output.with_suffix(".status.json")
    write_predictions(output, [protocol.prediction(row["instance_id"], f"bid-pilot-{arm}", "")])
    write_status(status_path, status="running")
    from bid import harness

    config = bid_config(workspace, endpoint, model_name, seed=seed,
                        task_statement=row["problem_statement"])
    patch = ""
    terminal = "ok"
    error = ""
    try:
        backend = harness.create_backend(config)
        if arm == "bid":
            patch = run_bid(workspace, row, config, backend, run_executor=run_executor,
                            extract_patch_fn=extract_patch_fn)
        else:
            patch = run_worker(workspace, row, config, backend, run_executor=run_executor,
                               extract_patch_fn=extract_patch_fn)
    except Exception as exc:
        terminal = "error"
        error = str(exc)
    finally:
        if not patch and extract_patch_fn:
            try:
                patch = extract_patch_fn()
            except Exception as exc:
                error = (error + f"; patch extraction failed: {exc}").strip("; ")
                terminal = "error"
        predicted = protocol.prediction(row["instance_id"], f"bid-pilot-{arm}", patch)
        write_predictions(output, [predicted])
        write_status(status_path, status=terminal, error=error, patch=patch)
        return {"status": terminal, "error": error, "prediction": predicted}


def self_test():
    """Fast, model-free proof that checkout/patch/allowlist behavior holds."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        source = root / "source"
        source.mkdir()
        _git(["init", "--quiet"], cwd=source)
        (source / "core.py").write_text("value = 1\n", encoding="utf-8")
        (source / "docs").mkdir()
        (source / "docs" / "conf.py").write_text("project = 'x'\n", encoding="utf-8")
        _git(["add", "."], cwd=source)
        _git(["-c", "user.email=t@example.com", "-c", "user.name=t", "commit", "-qm", "base"], cwd=source)
        base = _git(["rev-parse", "HEAD"], cwd=source).stdout.strip()

        checkout = clone_checkout(source, base, root / "checkout")
        row = {
            "instance_id": "repo__project-1",
            "repo": "repo/project",
            "base_commit": base,
            "problem_statement": "Fix core.py",
        }
        assert "Fix core.py" in public_prompt(row)
        (checkout / "core.py").write_text("value = 2\n", encoding="utf-8")
        (checkout / "docs" / "conf.py").write_text("project = 'y'\n", encoding="utf-8")
        (checkout / ".bid").mkdir()
        (checkout / ".bid" / "log.md").write_text("control\n", encoding="utf-8")
        (checkout / "docs" / "todo.md").write_text("control\n", encoding="utf-8")
        patch = extract_patch(checkout, base)
        assert "core.py" in patch and "docs/conf.py" in patch
        assert ".bid/" not in patch and "docs/todo.md" not in patch
        predicted = protocol.prediction(row["instance_id"], "bid", patch)
        assert "core.py" in predicted["model_patch"]
        print("runner self-test OK")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--arm", choices=("bid", "worker"))
    parser.add_argument("--task-json")
    parser.add_argument("--workspace")
    parser.add_argument("--endpoint", default="http://127.0.0.1:8081/v1/chat/completions")
    parser.add_argument("--model", default="Qwen3-4B-Instruct-2507-Q8_0")
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--output", default="/work/prediction.json")
    parser.add_argument("--executor-image", default=None)
    parser.add_argument("--executor-project", default=None)
    parser.add_argument("--executor-cell", default="adhoc")
    parser.add_argument("--host-extract", action="store_true",
                        help="trusted deterministic self-tests only; never for model output")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    if not (args.arm and args.task_json and args.workspace):
        parser.error("--arm, --task-json, and --workspace are required")
    row = load_task(args.task_json)
    executor = None
    if args.executor_image and args.executor_project:
        executor = per_run_executor(args.executor_image, args.executor_project, args.executor_cell)
    predicted = run_cell(args.arm, args.workspace, row, args.endpoint, args.model,
                         args.seed, args.output, run_executor=executor,
                         defer_extract=not args.host_extract)
    print(json.dumps(predicted, sort_keys=True))
    if predicted.get("status") != "ok":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
