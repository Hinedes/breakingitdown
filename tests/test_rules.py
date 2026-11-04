"""A rule without evidence is a wish: every rule in bid/rules.py must name something that still exists."""
import sys
from pathlib import Path

from bid.rules import LEVELS, RULES

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "evaluation" / "rule_probes"))
import probes  # noqa: E402


def _squash(text):
    return " ".join(text.split())


def _source(*patterns):
    return _squash("\n".join(p.read_text(encoding="utf-8") for pattern in patterns for p in sorted(ROOT.glob(pattern))))


def test_rule_ids_are_unique_and_levels_valid():
    assert len({rule.id for rule in RULES}) == len(RULES)
    assert {rule.level for rule in RULES} <= set(LEVELS)
    assert {rule.role for rule in RULES} == {"manager", "worker"}


def test_hard_rules_name_a_test_that_exists():
    tests = _source("tests/*.py")
    for rule in RULES:
        if rule.level in ("tolerant", "gate"):
            assert f"def {rule.evidence}(" in tests, f"{rule.id}: no test named {rule.evidence}"


def test_prompt_rules_have_a_probe_and_a_live_phrase():
    prompt_text = _source("prompts/*.md", "bid/*.py")
    for rule in RULES:
        if rule.level == "prompt":
            assert rule.evidence.startswith("probe:"), rule.id
            name = rule.evidence.split(":", 1)[1]
            assert probes.PROBES[name][0] == rule.id, f"{rule.id}: probe {name} measures another rule"
            assert rule.phrase and _squash(rule.phrase) in prompt_text, f"{rule.id}: prompt no longer says it"
