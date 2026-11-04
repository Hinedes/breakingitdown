"""Rootless, network-isolated Python execution for the study's hidden oracles."""

import os
import re
import shutil
import signal
import subprocess
import tempfile
import time


_SETUP = r"""
set -euo pipefail
mount --make-rprivate /
root="$1"
stage="$2"
python_home="$3"
code="$4"
mkdir -p "$root"
mount -t tmpfs -o size=256m,nr_inodes=8192 tmpfs "$root"
mkdir -p "$root/opt/python/bin" "$root/opt/python/lib" \
    "$root/lib/x86_64-linux-gnu" "$root/lib64" "$root/work" "$root/tmp" "$root/dev"
cp "$python_home/bin/python3.12" "$root/opt/python/bin/"
cp -a "$python_home/lib/." "$root/opt/python/lib/"
for lib in libpthread.so.0 libdl.so.2 libutil.so.1 libm.so.6 librt.so.1 libc.so.6; do
    cp -L "/lib/x86_64-linux-gnu/$lib" "$root/lib/x86_64-linux-gnu/$lib"
done
cp -L /lib64/ld-linux-x86-64.so.2 "$root/lib64/ld-linux-x86-64.so.2"
mount -t tmpfs -o size=64m,nr_inodes=4096 tmpfs "$root/work"
cp -a "$stage/." "$root/work/"
mount -t tmpfs -o size=64m,nr_inodes=4096 tmpfs "$root/tmp"
/usr/sbin/chroot "$root" /opt/python/bin/python3.12 -I -S -B -c "$code"
"""


_WORKER_SETUP = r"""
set -euo pipefail
mount --make-rprivate /
readonly_tree() {
    # ponytail: Q12 is x86_64/Linux 5.12+; fail closed elsewhere, never fall back to writable mounts.
    /usr/bin/python3 -c 'import ctypes,os,platform,sys; assert platform.machine()=="x86_64", "unsupported sandbox architecture"; c=ctypes.CDLL(None,use_errno=True); a=(ctypes.c_uint64*4)(7,0,0,0); result=c.syscall(442,-100,sys.argv[1].encode(),int(sys.argv[2]),ctypes.byref(a),ctypes.sizeof(a)); assert result==0, "read-only mount failed: "+str(ctypes.get_errno())' "$1" "${2:-32768}"
}
root="$1"
workspace="$2"
python_home="$3"
launcher="$4"
shift 4
mkdir -p "$root"
mount -t tmpfs -o size=16m,nr_inodes=2048 tmpfs "$root"
mkdir -p "$root/usr" "$root/opt/python" "$root/work" "$root/tmp" "$root/dev" "$root/empty"
mount --rbind /usr "$root/usr"
readonly_tree "$root/usr"
ln -s usr/bin "$root/bin"
ln -s usr/sbin "$root/sbin"
ln -s usr/lib "$root/lib"
ln -s usr/lib64 "$root/lib64"
mount --bind "$python_home" "$root/opt/python"
readonly_tree "$root/opt/python"
mount --bind "$workspace" "$root/work"
touch "$root/empty-file" "$root/dev/null"
mount --bind /dev/null "$root/dev/null"
for path in .bid .git docs/task.md docs/todo.md docs/manager.md docs/worker.md docs/project-status.md docs/decisions.md docs/reviews; do
    target="$root/work/$path"
    if [ -L "$target" ]; then
        echo 'protected path is a symlink' >&2
        exit 126
    elif [ -d "$target" ]; then
        mount --bind "$root/empty" "$target"
        readonly_tree "$target"
    elif [ -e "$target" ]; then
        mount --bind "$root/empty-file" "$target"
        readonly_tree "$target"
    fi
done
mount -t tmpfs -o size=64m,nr_inodes=4096 tmpfs "$root/tmp"
readonly_tree "$root" 0
exec /usr/sbin/chroot "$root" /opt/python/bin/python3.12 -I -S -B -c "$launcher" "$@"
"""


_WORKER_LAUNCH = r"""
import ctypes, errno, os, subprocess, sys
libc = ctypes.CDLL(None, use_errno=True)
assert libc.prctl(38, 1, 0, 0, 0) == 0, "no-new-privileges failed"
for capability in range(64):
    result = libc.prctl(24, capability, 0, 0, 0)
    assert result == 0 or ctypes.get_errno() == errno.EINVAL, "capability bounding drop failed"
header = (ctypes.c_uint32 * 2)(0x20080522, 0)
data = (ctypes.c_uint32 * 6)()
assert libc.capset(ctypes.byref(header), ctypes.byref(data)) == 0, "capability clearing failed"
assert libc.prctl(1, 9, 0, 0, 0) == 0, "parent-death protection failed"
assert libc.prctl(4, 0, 0, 0, 0) == 0, "supervisor tracing protection failed"
os.chdir("/work")
process = subprocess.Popen(sys.argv[1:], env={
    "PATH": "/opt/python/bin:/usr/bin:/bin", "HOME": "/tmp", "TMPDIR": "/tmp",
    "LANG": "C.UTF-8", "PYTHONDONTWRITEBYTECODE": "1",
})
status = process.wait()
raise SystemExit(status if status >= 0 else 128 - status)
"""


def _limits():
    import resource

    resource.setrlimit(resource.RLIMIT_CPU, (15, 15))
    resource.setrlimit(resource.RLIMIT_AS, (512 * 1024**2, 512 * 1024**2))
    # The inference server's threads share this UID; 64 blocked unshare in WSL.
    resource.setrlimit(resource.RLIMIT_NPROC, (128, 128))
    resource.setrlimit(resource.RLIMIT_FSIZE, (64 * 1024**2, 64 * 1024**2))
    resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


def run_command(workspace, argv, python_home, temp_root, timeout=25):
    """Bind only the disposable project writable; never merge an untrusted tree."""
    workspace = os.path.realpath(workspace)
    argv = list(argv)
    if not argv or not os.path.isdir(workspace):
        raise ValueError("command and existing disposable workspace required")
    if os.path.basename(argv[0]) in {"python", "python3", "python3.12"}:
        argv[0] = "/opt/python/bin/python3.12"
    with tempfile.TemporaryDirectory(prefix="worker-", dir=temp_root) as temp:
        root = os.path.join(temp, "root")
        args = [
            "unshare", "--user", "--map-root-user", "--mount", "--net", "--ipc", "--uts", "--pid", "--fork", "--kill-child=KILL",
            "/bin/bash", "-c", _WORKER_SETUP, "worker", root, workspace,
            os.path.realpath(python_home), _WORKER_LAUNCH, *argv,
        ]
        result = _execute(args, timeout)
    if result["timed_out"]:
        raise subprocess.TimeoutExpired(argv, timeout, result["stdout"], result["stderr"])
    return subprocess.CompletedProcess(argv, result["returncode"], result["stdout"], result["stderr"])


def _execute(args, timeout):
    env = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "HOME": "/tmp", "TMPDIR": "/tmp", "LANG": "C.UTF-8"}
    started = time.monotonic()
    with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
        process = subprocess.Popen(
            args, env=env, stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr,
            start_new_session=True, preexec_fn=_limits,
        )
        timed_out = False
        try:
            returncode = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
            returncode = process.returncode
        stdout.seek(0)
        stderr.seek(0)
        out = stdout.read(4 * 1024**2).decode("utf-8", "replace")
        err = stderr.read(4 * 1024**2).decode("utf-8", "replace")
    return {"returncode": returncode, "timed_out": timed_out,
            "wall_s": round(time.monotonic() - started, 3), "stdout": out, "stderr": err}


def run_python(workspace, code, python_home, temp_root, timeout=25):
    """Run oracle code against a copied workspace, never the live workspace."""
    with tempfile.TemporaryDirectory(prefix="oracle-", dir=temp_root) as temp:
        root = os.path.join(temp, "root")
        stage = os.path.join(temp, "workspace")
        shutil.copytree(
            workspace,
            stage,
            symlinks=True,
            ignore=shutil.ignore_patterns(
                ".bid", ".git", "__pycache__", ".pytest_cache", "*.pyc", ".env"
            ),
        )
        args = [
            "unshare", "--user", "--map-root-user", "--mount", "--net", "--ipc", "--uts", "--pid", "--fork", "--kill-child=KILL",
            "/bin/bash", "-c", _SETUP, "oracle", root, stage, python_home, code,
        ]
        result = _execute(args, timeout)
        result["stderr"] = re.sub(r"/home/iclab/[^\s:'\"]+", "<REMOTE_PATH>", result["stderr"])
        return result
