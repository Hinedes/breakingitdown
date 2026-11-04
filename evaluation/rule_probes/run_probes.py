"""Run every probe N times against a live llama.cpp endpoint and print adherence per rule.

    python run_probes.py --endpoint http://127.0.0.1:8081/v1/chat/completions --model MODEL --n 10 --out out.json
"""
import argparse
import functools
import glob
import json
import sys

import probes
from bid import model as model_mod


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--n", type=int, default=10)
    parser.add_argument("--out")
    parser.add_argument("--issues-glob", help="task.json files whose problem_statement feeds the Manager probe")
    parser.add_argument("--only", help="run just this probe")
    args = parser.parse_args()
    issues = None
    if args.issues_glob:
        found = {json.load(open(path, encoding="utf-8"))["problem_statement"] for path in sorted(glob.glob(args.issues_glob))}
        issues = tuple(sorted(found))

    def make_backend(trial):
        return model_mod.LlamaCppBackend(endpoint=args.endpoint, model=args.model, timeout=120,
                                         max_tokens=1024, seed=trial)

    report = {}
    for name, (rule, runner, passes) in probes.PROBES.items():
        if args.only and name != args.only:
            continue
        if issues and runner is probes.run_manager:
            runner = functools.partial(runner, issues=issues)
        trials = [runner(make_backend, trial) for trial in range(args.n)]
        report[name] = {"rule": rule, "passed": sum(1 for t in trials if passes(t)), "n": args.n, "trials": trials}
        print(f"{rule:36s} {report[name]['passed']}/{args.n}", flush=True)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as file:
            json.dump(report, file, indent=2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
