"""Stage the v1/merge source, compute its pilot tree hash, and seal a new manifest pinned to it."""
import hashlib, json, os, pathlib, subprocess, sys, tarfile

REPO = pathlib.Path(r"C:\projects\breakingitdown")
OUT = pathlib.Path(sys.argv[1])
STAGE = OUT / "stage"
OUT.mkdir(parents=True, exist_ok=True)
if STAGE.exists():
    raise SystemExit("stage exists; remove it first")

head = subprocess.run(["git", "-C", str(REPO), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
dirty = subprocess.run(["git", "-C", str(REPO), "status", "--porcelain", "--", "bid", "prompts", "pyproject.toml"],
                       capture_output=True, text=True).stdout.strip()
assert not dirty, f"uncommitted product changes: {dirty}"

# bid/, prompts/, pyproject.toml exactly as committed
STAGE.mkdir(parents=True)
archive = subprocess.run(["git", "-C", str(REPO), "archive", "--format=tar", "HEAD", "bid", "prompts", "pyproject.toml"],
                         capture_output=True).stdout
tmp = OUT / "src.tar"
tmp.write_bytes(archive)
with tarfile.open(tmp) as tf:
    tf.extractall(STAGE)
tmp.unlink()
# the pilot tooling is part of the frozen source (pilot.source_tree_hash includes its *.py files)
dst = STAGE / "evaluation" / "swebench-pilot"
dst.mkdir(parents=True)
for p in sorted((REPO / "evaluation" / "swebench-pilot").glob("*.py")):
    (dst / p.name).write_bytes(p.read_bytes().replace(b"\r\n", b"\n"))  # LF, like the committed product files

sys.path.insert(0, str(dst))
sys.dont_write_bytecode = True
import pilot, protocol  # noqa: E402

tree = pilot.source_tree_hash(str(STAGE))

# fingerprints, same format as the earlier freezes
rels = []
for base in ("bid", "prompts"):
    for dirpath, dirnames, filenames in os.walk(STAGE / base):
        dirnames[:] = sorted(d for d in dirnames if d != "__pycache__")
        for name in sorted(filenames):
            if not name.endswith((".pyc", ".pyo")):
                rels.append(os.path.relpath(os.path.join(dirpath, name), STAGE).replace(os.sep, "/"))
rels.append("pyproject.toml")
rels += ["evaluation/swebench-pilot/" + n for n in sorted(os.listdir(dst)) if n.endswith(".py")]
rels = sorted(set(rels))
lines = [f"{hashlib.sha256((STAGE / r).read_bytes()).hexdigest()}  {r}" for r in rels]
(STAGE / "SOURCE_FINGERPRINTS.txt").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
(STAGE / "SOURCE_FINGERPRINTS.sha256").write_text(f"{tree}  SOURCE_FINGERPRINTS.txt\n", encoding="utf-8", newline="\n")
assert pilot.source_tree_hash(str(STAGE)) == tree

# manifest: same frozen sample/budgets, new source pin; previous pin kept as provenance
manifest = json.loads((REPO / "evaluation" / "swebench-pilot" / "manifest.json").read_text(encoding="utf-8"))
old_source, old_manifest = manifest["pins"]["bid_source_sha256"], manifest["manifest_sha256"]
pins = manifest["pins"]
pins["previous_bid_source_sha256"] = old_source
pins["previous_manifest_sha256"] = old_manifest
pins["bid_source_sha256"] = tree
pins["bid_git_commit"] = head
body = {k: v for k, v in manifest.items() if k != "manifest_sha256"}
sealed = protocol.seal_manifest(body)
name = f"manifest-{tree[:12]}.json"
(OUT / name).write_text(json.dumps(sealed, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")

# tarball for deployment
tar_path = OUT / f"bid-source-{tree[:12]}.tar"
with tarfile.open(tar_path, "w", format=tarfile.GNU_FORMAT) as tf:
    for r in rels + ["SOURCE_FINGERPRINTS.txt", "SOURCE_FINGERPRINTS.sha256"]:
        tf.add(STAGE / r, arcname=r, recursive=False)

print(json.dumps({"git": head, "files": len(rels), "tree": tree, "previous_tree": old_source,
                  "manifest": name, "manifest_sha256": sealed["manifest_sha256"],
                  "tar": tar_path.name, "tar_bytes": tar_path.stat().st_size,
                  "tar_sha256": hashlib.sha256(tar_path.read_bytes()).hexdigest()}, indent=2))
