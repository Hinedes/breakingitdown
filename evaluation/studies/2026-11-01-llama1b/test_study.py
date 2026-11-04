import os
import pathlib
import shutil
import sqlite3
import sys
import tempfile
import time

import pytest
import oracles
import study
from bid import todo
from sandbox import run_command, run_python


STUDY = pathlib.Path(__file__).parent
PYTHON_HOME = os.environ.get(
    "BID_STUDY_PYTHON", "/home/iclab/bid-llama-runtime/tools/python312/python"
)
PYTHON_AVAILABLE = (
    os.name == "posix"
    and pathlib.Path(PYTHON_HOME, "bin", "python3.12").is_file()
)


def test_queue_has_three_repeats_and_shared_direct_references():
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE queue (run_id TEXT, task TEXT, repeat INTEGER, condition TEXT, paired_with TEXT, status TEXT, run_dir TEXT, result TEXT)")
    study._schedule(db)
    rows = db.execute("SELECT task,repeat,condition FROM queue").fetchall()
    assert len(rows) == 45
    for condition in study.CONDITIONS:
        assert sum(row[2] == condition for row in rows) == 9
    for task in oracles.TASKS:
        for repeat in range(1, 4):
            cells = [row[2] for row in rows if row[:2] == (task, repeat)]
            assert len(cells) == 5
            assert cells.count("D128") == cells.count("D64") == 1


@pytest.mark.skipif(os.name != "posix", reason="Linux process state is required")
def test_owned_server_process_state_is_live():
    assert study._process_state(os.getpid()) not in {None, "Z", "X"}


def test_server_switch_only_accepts_known_local_model_process():
    binary = "/owned/llama-server"
    args = [binary, "--model", "/home/iclab/bid-models/Llama-3.2-3B-Instruct-Q8_0.gguf", "--host", "127.0.0.1", "--port", "8081"]
    assert study.owned_server_command(args, binary)
    unknown = list(args)
    unknown[2] = "/other/user/model.gguf"
    assert not study.owned_server_command(unknown, binary)
    assert not study.owned_server_command(args + ["--host", "0.0.0.0"], binary)


@pytest.mark.skipif(os.name != "posix", reason="POSIX process groups are required")
def test_supervisor_kills_child_process_group_on_timeout(tmp_path):
    pid_file = tmp_path / "child.pid"
    child = "import subprocess,sys,time; p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)']); open(sys.argv[1],'w').write(str(p.pid)); time.sleep(60)"
    result = study._supervise(
        [sys.executable, "-c", child, str(pid_file)], 0.3,
        tmp_path / "supervisor.log", tmp_path, terminate_grace=0.5,
    )
    assert result["timed_out"]
    assert result["process_group_cleaned"]
    child_pid = int(pid_file.read_text())
    for _ in range(20):
        if study._process_state(child_pid) in {None, "Z", "X"}:
            break
        time.sleep(0.05)
    assert study._process_state(child_pid) in {None, "Z", "X"}


def test_custom_direct_todo_contains_complete_task_spec():
    with tempfile.TemporaryDirectory() as temp:
        workspace = pathlib.Path(temp) / "workspace"
        workspace.mkdir()
        study._direct_init("pipeline", {"workspace": str(workspace)})
        parsed = todo.parse_todo((workspace / "docs/todo.md").read_text(encoding="utf-8"))
        description = parsed[0]["description"]
        assert "sales_loader.py" in description
        assert "unit_price_cents" in description
        assert "sales_summary.py" in description
        assert "\n" not in description


def test_source_manifest_resolves_immutable_epoch_not_shared_root(tmp_path, monkeypatch):
    epoch = tmp_path / "epoch-10"
    source = epoch / "source" / "evaluation" / "studies" / "study"
    shared = tmp_path / "SOURCE_MANIFEST.json"
    (epoch / "SOURCE_MANIFEST.json").parent.mkdir(parents=True)
    (epoch / "SOURCE_MANIFEST.json").write_text("epoch manifest")
    shared.write_text("mutable shared manifest")
    source.mkdir(parents=True)
    monkeypatch.setattr(study, "ROOT", source)
    monkeypatch.delenv("BID_SOURCE_MANIFEST", raising=False)
    assert study.source_manifest_path() == epoch / "SOURCE_MANIFEST.json"


@pytest.mark.skipif(os.name != "posix", reason="symlink support required")
def test_artifact_hashes_never_read_outside_symlink_targets(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / "private.txt"
    secret.write_text("first")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "link").symlink_to(secret)
    (workspace / "directory").symlink_to(outside, target_is_directory=True)
    first = study.tree_hash(workspace)
    secret.write_text("different private content")
    assert study.tree_hash(workspace) == first
    assert len(study.workspace_entries(workspace)) == 2
    with pytest.raises(ValueError, match="symlink"):
        study.digest(workspace / "link")


def test_reconciliation_grammar_uses_only_active_task_ids():
    from protocol import reconciliation_grammar
    messages = [
        {"role": "system", "content": "Manager"},
        {"role": "user", "content": "Current checklist:\n- [x] T1 - old\n- [-] T2 - current\n- [ ] T3 - future\n\nProvisional submissions:\n"},
    ]
    grammar = reconciliation_grammar(messages)
    assert "T2" in grammar and "T1" not in grammar and "T3" not in grammar
    assert "# Rework" in grammar and "CONTINUE" in grammar
    assert "COMPLETE" not in grammar


@pytest.mark.skipif(not PYTHON_AVAILABLE, reason="study Python runtime unavailable")
def test_sandbox_hides_host_home_and_network_and_uses_workspace_copy():
    with tempfile.TemporaryDirectory() as temp:
        workspace = pathlib.Path(temp) / "workspace"
        workspace.mkdir()
        (workspace / "input.txt").write_text("study fixture", encoding="utf-8")
        result = run_python(
            workspace,
            "import os,socket; "
            "assert not os.path.exists('/home/iclab/bid-llama-runtime/server.pid'); "
            "assert open('/work/input.txt').read() == 'study fixture'; "
            "\ntry: socket.create_connection(('127.0.0.1',8081),timeout=.2); "
            "raise AssertionError('host loopback reachable')\n"
            "except OSError: pass\n"
            "open('/work/result.txt','w').write('sandbox-ok'); print('SANDBOX_OK')",
            PYTHON_HOME,
            temp,
        )
        assert result["returncode"] == 0, result
        assert result["stdout"].strip() == "SANDBOX_OK"
        assert not (workspace / "result.txt").exists()


@pytest.mark.skipif(not PYTHON_AVAILABLE, reason="study Python runtime unavailable")
def test_worker_executor_persists_only_disposable_workspace(tmp_path):
    workspace = tmp_path / "workspace"
    (workspace / "docs").mkdir(parents=True)
    (workspace / ".bid").mkdir()
    (workspace / ".bid" / "control").write_text("private control", encoding="utf-8")
    (workspace / "docs" / "task.md").write_text("protected task", encoding="utf-8")
    outside = tmp_path / "outside.txt"
    outside.write_text("outside sentinel", encoding="utf-8")
    (workspace / "outside-link").symlink_to(outside)
    code = (
        "import os,pathlib,socket,subprocess; "
        f"assert not pathlib.Path({str(outside)!r}).exists(); "
        "assert not pathlib.Path('/home/iclab').exists(); "
        "assert not pathlib.Path('/proc').exists(); "
        "assert not os.environ.get('HF_TOKEN'); "
        "assert not pathlib.Path('/work/outside-link').exists(); "
        "assert list(pathlib.Path('/work/.bid').iterdir()) == []; "
        "assert pathlib.Path('/work/docs/task.md').read_text() == ''; "
        "assert subprocess.run(['mount','-o','remount,rw','/opt/python'],capture_output=True).returncode != 0; "
        "assert subprocess.run(['unshare','--user','--map-root-user','--mount','/bin/sh','-c','mount -o remount,bind,rw /opt/python'],capture_output=True).returncode != 0; "
        "pathlib.Path('/work/output.txt').write_text('persisted'); "
        "\ntry: pathlib.Path('/work/docs/task.md').write_text('attack'); raise AssertionError('protected write')\n"
        "except OSError: pass\n"
        "try: pathlib.Path('/opt/python/escape.txt').write_text('attack'); raise AssertionError('runtime write')\n"
        "except OSError: pass\n"
        "try: socket.create_connection(('127.0.0.1',8081),timeout=.2); raise AssertionError('network')\n"
        "except OSError: pass\n"
        "print('ISOLATED')"
    )
    result = run_command(workspace, ["python", "-c", code], PYTHON_HOME, tmp_path)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ISOLATED"
    assert (workspace / "output.txt").read_text() == "persisted"
    assert (workspace / "docs" / "task.md").read_text() == "protected task"
    assert (workspace / ".bid" / "control").read_text() == "private control"
    assert outside.read_text() == "outside sentinel"


@pytest.mark.skipif(not PYTHON_AVAILABLE, reason="study Python runtime unavailable")
def test_worker_executor_timeout_kills_background_descendants(tmp_path):
    import subprocess
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    code = (
        "import os,subprocess,sys,time; os.setsid(); "
        "subprocess.Popen([sys.executable,'-c',\"import time,pathlib;time.sleep(1);pathlib.Path('/work/escaped.txt').write_text('bad')\"]); "
        "time.sleep(20)"
    )
    with pytest.raises(subprocess.TimeoutExpired):
        run_command(workspace, ["python", "-c", code], PYTHON_HOME, tmp_path, timeout=.2)
    time.sleep(1.1)
    assert not (workspace / "escaped.txt").exists()


@pytest.mark.skipif(not PYTHON_AVAILABLE, reason="study Python runtime unavailable")
def test_frozen_oracles_reject_seeds_and_accept_reference_artifacts():
    with tempfile.TemporaryDirectory() as temp:
        temp = pathlib.Path(temp)
        for task in oracles.TASKS:
            seed = STUDY / "tasks" / task
            workspace = temp / task
            workspace.mkdir()
            for source in seed.rglob("*"):
                if not source.is_file() or source.name == "task.md":
                    continue
                target = workspace / source.relative_to(seed)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
            baseline = oracles.evaluate(task, workspace, PYTHON_HOME, temp)
            assert not baseline["binary_pass"], (task, baseline)

            if task == "easy":
                (workspace / "word_stats.py").write_text(
                    "def count_words(text): return len(text.split())\n"
                    "def longest_word(text): return max(text.split(), key=len, default=None)\n",
                    encoding="utf-8",
                )
                tests = workspace / "tests" / "test_word_stats.py"
                tests.parent.mkdir(parents=True, exist_ok=True)
                tests.write_text("def test_words(): pass\n", encoding="utf-8")
                (workspace / "README.md").write_text(
                    "count_words(text); longest_word(text)\n", encoding="utf-8"
                )
            elif task == "pipeline":
                (workspace / "sales_loader.py").write_text(
                    "import csv\n"
                    "def load_sales(path):\n"
                    " with open(path, newline='', encoding='utf-8') as f:\n"
                    "  return [{'date':r['date'],'sku':r['sku'],'quantity':int(r['quantity']),"
                    "'unit_price_cents':int(r['unit_price_cents'])} for r in csv.DictReader(f)]\n",
                    encoding="utf-8",
                )
                (workspace / "sales_filter.py").write_text(
                    "def filter_sales(rows,start_date,end_date):\n"
                    " return [r for r in rows if start_date <= r['date'] <= end_date and r['quantity'] > 0]\n",
                    encoding="utf-8",
                )
                (workspace / "sales_summary.py").write_text(
                    "def summarize_sales(rows):\n"
                    " d={}\n"
                    " for r in rows:\n"
                    "  x=d.setdefault(r['sku'],{'sku':r['sku'],'units':0,'revenue_cents':0})\n"
                    "  x['units']+=r['quantity']; x['revenue_cents']+=r['quantity']*r['unit_price_cents']\n"
                    " return [d[k] for k in sorted(d)]\n",
                    encoding="utf-8",
                )
                (workspace / "sales_pipeline.py").write_text(
                    "from sales_loader import load_sales\nfrom sales_filter import filter_sales\n"
                    "from sales_summary import summarize_sales\n"
                    "def build_sales_report(path,start_date,end_date):\n"
                    " return summarize_sales(filter_sales(load_sales(path),start_date,end_date))\n",
                    encoding="utf-8",
                )
                tests = workspace / "tests" / "test_sales_pipeline.py"
                tests.parent.mkdir(parents=True, exist_ok=True)
                tests.write_text("def test_report(): pass\n", encoding="utf-8")
            else:
                (workspace / "range_utils.py").write_text(
                    "def merge_ranges(ranges):\n"
                    " result=[]\n"
                    " for start,end in sorted(ranges):\n"
                    "  if result and start <= result[-1][1]: result[-1][1]=max(result[-1][1],end)\n"
                    "  else: result.append([start,end])\n"
                    " return result\n",
                    encoding="utf-8",
                )
                tests = workspace / "tests" / "test_range_utils.py"
                tests.parent.mkdir(parents=True, exist_ok=True)
                tests.write_text("def test_unsorted(): pass\n", encoding="utf-8")

            accepted = oracles.evaluate(task, workspace, PYTHON_HOME, temp)
            assert accepted["binary_pass"], (task, accepted)
