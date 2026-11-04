import pathlib, re, sys
src = pathlib.Path(r"C:\Users\hind\AppData\Local\Temp\opencode\bid_swe_remainder_v2.py").read_text(encoding="utf-8")
out = pathlib.Path(sys.argv[1])
tree, manifest_sha = sys.argv[2], sys.argv[3]
t12 = tree[:12]
s = src


def swap(old, new):
    global s
    assert s.count(old) == 1, (s.count(old), old[:70])
    s = s.replace(old, new)


# header
a = s.index('"""TEMP continuation')
b = s.index('"""', a + 3) + 3
s = s[:a] + f'''"""BID v1 SWE-bench pilot launcher (all 60 cells, fresh run directory).

Frozen source {t12}, sealed manifest, 20 gold/base-gated instances, fixed Qwen3-4B Q8 server.
Pauses (does not retry) on any infrastructure fault; safe to re-run to resume: completed
checkpoints are skipped by pilot.run_one.

    python3 bid_swe_v1.py --preflight-only
    python3 bid_swe_v1.py
"""''' + s[b:]

swap('SRC = BASE / "bid-source-c63701b9c9c6"', f'SRC = BASE / "bid-source-{t12}"')
swap('MANIFEST = BASE / "manifest-c63701b9c9c6.json"', f'MANIFEST = BASE / "manifest-{t12}.json"')
swap('RUNS = BASE / "runs-export-v2"', 'RUNS = BASE / "runs-v1"')
swap('RUNTIME = BASE / "runtime" / "remainder-v2-2026-11-02"', 'RUNTIME = BASE / "runtime" / "v1-2026-11-03"')
swap('SOURCE_SHA = "c63701b9c9c623053ed387f816738b411071ef74a6a7685f1cb3ab39058817da"', f'SOURCE_SHA = "{tree}"')
swap('MANIFEST_SHA = "b0cf7f6d2b3dfddbd9e769762d00d6569b88b31a9d0a420b87685d424d33b03d"', f'MANIFEST_SHA = "{manifest_sha}"')

# no derived prefix: a fresh run directory, resumable
a = s.index("DERIVED = [")
b = s.index("]\n", a) + 2
s = s[:a] + "DERIVED = []\n" + s[b:]
a = s.index("def plan_requirement(existing_keys):")
b = s.index("def preflight_gates():")
s = s[:a] + '''def plan_requirement(existing_keys):
    unexpected = sorted(k for k in existing_keys if "--" not in k)
    return {"present": len(existing_keys), "missing": 60 - len(existing_keys),
            "unexpected": unexpected, "ok": not unexpected}


''' + s[b:]
a = s.index("def preflight_derived():")
b = s.index("def check_live_state(strict):")
s = s[:a] + '''def preflight_derived():
    RUNS.mkdir(parents=True, exist_ok=True)
    existing = {p.name for p in RUNS.iterdir() if p.is_dir() and (p / "complete.json").exists()}
    plan = plan_requirement(existing)
    if not plan["ok"]:
        raise Refusal(f"run directory has unexpected entries: {plan}")
    return [], plan


''' + s[b:]
swap('''    if (RUNTIME / "launched.json").exists():
        raise Refusal("continuation runtime already launched; refusing to rerun")
''', '')
swap('''    (RUNTIME / "launched.json").write_text(json.dumps({"at": utcnow(), "pid": os.getpid()}) + "\\n",
                                           encoding="utf-8")''',
     '''    (RUNTIME / f"launched-{utcnow()}.json").write_text(json.dumps({"at": utcnow(), "pid": os.getpid()}) + "\\n",
                                                       encoding="utf-8")''')

# self check: drop the derived-plan assertions
a = s.index("    plan = plan_requirement(set(DERIVED))")
b = s.index("    def cell(grade")
s = s[:a] + '''    assert plan_requirement(set())["ok"] and plan_requirement({"x"})["ok"] is False
    assert plan_requirement({"django__django-10097--bid"})["missing"] == 59

''' + s[b:]
out.write_text(s, encoding="utf-8", newline="\n")
print("launcher written", len(s.splitlines()), "lines")
