"""Deterministic external evaluator for the long-horizon pipeline task.

Pass requires ALL checks:
1. All five modules import cleanly (chain: schema <- loader <- filters <- report <- pipeline)
2. schema.Record and schema.validate work (valid + invalid sets)
3. loader.load parses CSV, skips blanks/#, raises on malformed
4. filters.by_category and filters.total
5. report.make_report exact formatting
6. sample_data.csv exists with >=6 records, >=3 categories, a # comment
7. End-to-end: pipeline.run on the actual sample_data.csv with category "food"
   returns exactly the food lines in file order
"""
import importlib.util
import os
import sys

WS = os.environ.get("LH_WS", ".")
sys.path.insert(0, WS)
errors = []

mods = {}
for name in ("schema", "loader", "filters", "report", "pipeline"):
    spec = importlib.util.spec_from_file_location(name, os.path.join(WS, f"{name}.py"))
    m = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(m)
        mods[name] = m
        errors.append(f"PASS: {name}.py imports cleanly")
    except Exception as exc:
        errors.append(f"FAIL: {name}.py import: {type(exc).__name__}: {exc}")
        print("\n".join(errors))
        sys.exit(1)

# 2. schema
try:
    R = mods["schema"].Record
    r1 = R("apple", "food", 1.5)
    ok = r1.name == "apple" and r1.category == "food" and r1.amount == 1.5
    errors.append(f"{'PASS' if ok else 'FAIL'}: Record fields (name/category/amount)")
    ok = mods["schema"].validate([r1])
    errors.append(f"{'PASS' if ok else 'FAIL'}: validate accepts valid record")
    ok = not mods["schema"].validate([R("", "food", 1.5)])
    errors.append(f"{'PASS' if ok else 'FAIL'}: validate rejects empty name")
    ok = not mods["schema"].validate([R("x", "food", 0)])
    errors.append(f"{'PASS' if ok else 'FAIL'}: validate rejects amount <= 0")
except Exception as exc:
    errors.append(f"FAIL: schema checks raised {type(exc).__name__}: {exc}")

# 3. loader
csv_ok = "apple,food,1.5\n# comment\n\nbanana,food,2.0\n"
try:
    recs = mods["loader"].load(csv_ok)
    ok = len(recs) == 2 and recs[0].name == "apple" and recs[1].amount == 2.0
    errors.append(f"{'PASS' if ok else 'FAIL'}: load parses CSV, skips comments/blanks")
    try:
        mods["loader"].load("a,b\n")
        errors.append("FAIL: load did not raise on malformed line")
    except ValueError:
        errors.append("PASS: load raises ValueError on malformed line")
    except Exception as exc:
        errors.append(f"FAIL: load raised {type(exc).__name__} instead of ValueError")
except Exception as exc:
    errors.append(f"FAIL: load raised {type(exc).__name__}: {exc}")

# 4. filters
try:
    recs = mods["loader"].load("apple,food,1.5\nball,toy,3.0\nbanana,food,2.0\n")
    food = mods["filters"].by_category(recs, "food")
    ok = len(food) == 2 and all(r.category == "food" for r in food)
    errors.append(f"{'PASS' if ok else 'FAIL'}: by_category filters correctly")
    ok = mods["filters"].total(recs) == 6.5
    errors.append(f"{'PASS' if ok else 'FAIL'}: total sums amounts")
except Exception as exc:
    errors.append(f"FAIL: filters raised {type(exc).__name__}: {exc}")

# 5. report
try:
    recs = mods["loader"].load("Apple,food,1.5\n")
    lines = mods["report"].make_report(recs)
    ok = lines == ["Apple (food): 1.50"]
    errors.append(f"{'PASS' if ok else 'FAIL'}: make_report formatting (got {lines!r})")
except Exception as exc:
    errors.append(f"FAIL: report raised {type(exc).__name__}: {exc}")

# 6. sample_data.csv
csv_path = os.path.join(WS, "sample_data.csv")
if os.path.exists(csv_path):
    txt = open(csv_path).read()
    lines = [l for l in txt.splitlines() if l.strip() and not l.strip().startswith("#")]
    cats = set()
    ok_rows = 0
    for l in lines:
        parts = l.split(",")
        if len(parts) == 3:
            ok_rows += 1
            cats.add(parts[1].strip())
    ok = ok_rows >= 6 and len(cats) >= 3 and any(l.strip().startswith("#") for l in txt.splitlines())
    errors.append(f"{'PASS' if ok else 'FAIL'}: sample_data.csv >=6 rows, >=3 categories, has comment (got {ok_rows} rows, {len(cats)} cats)")
else:
    errors.append("FAIL: sample_data.csv missing")

# 7. end-to-end
try:
    txt = open(csv_path).read()
    out = mods["pipeline"].run(txt, "food")
    expected = [l for l in txt.splitlines() if l.strip() and not l.strip().startswith("#")]
    food_lines = [l for l in expected if l.split(",")[1].strip() == "food"]
    want = [f"{l.split(',')[0].strip()} (food): {float(l.split(',')[2]):.2f}" for l in food_lines]
    ok = isinstance(out, list) and out == want
    errors.append(f"{'PASS' if ok else 'FAIL'}: pipeline.run end-to-end (got {len(out) if isinstance(out, list) else '?'} lines, want {len(want)})")
except Exception as exc:
    errors.append(f"FAIL: pipeline.run raised {type(exc).__name__}: {exc}")

print("\n".join(errors))
sys.exit(0 if all(e.startswith("PASS") for e in errors) else 1)
