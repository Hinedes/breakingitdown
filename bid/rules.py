"""Rulebook: every rule BID asks the Manager or Worker to follow, and how it is enforced.

A small model reads a prompt rule only partly, so a rule is only as reliable as its enforcement:

  prompt    only the prompt asks. Must name a probe (evaluation/rule_probes) that measures adherence
            on the real model, and its phrase must still appear in the prompt text.
  tolerant  the harness accepts any reasonable form and normalises it. Must name a test.
  gate      the harness refuses, rewrites or bounds the violation. Must name a test.

tests/test_rules.py fails when a rule loses its evidence. When a probe shows a prompt rule is broken,
promote it to tolerant or gate; do not add more prompt text (it does not move small models).
"""
from collections import namedtuple

Rule = namedtuple("Rule", "id role level text evidence phrase")

LEVELS = ("prompt", "tolerant", "gate")

RULES = (
    Rule("manager.checklist_lines", "manager", "tolerant",
         "Return the plan as `- [ ]` checklist lines; code shown under an item is folded into it.",
         "test_manager_folds_a_fenced_snippet_into_its_task", None),
    Rule("manager.no_commentary", "manager", "gate",
         "Commentary, headings, numbered lists and checked items reject the plan and trigger a retry.",
         "test_manager_rejects_commentary_and_invalid_items", None),
    Rule("manager.no_placeholders", "manager", "gate",
         "A placeholder description such as `Description` is not a task.",
         "test_manager_todo_rejects_placeholder_descriptions", None),
    Rule("manager.escalated_task_rewritten", "manager", "tolerant",
         "An escalated task is rewritten with # Replace Remaining Plan; a # Rework on it is read the same way.",
         "test_rework_on_an_escalated_task_is_read_as_a_rewrite", None),
    Rule("manager.names_target_file", "manager", "prompt",
         "Each item names the file or files it changes.",
         "probe:manager_names_target_file", "Each item must name its target file"),
    Rule("worker.no_guessed_paths", "worker", "gate",
         "Missing files are not guessed at: a second miss points to MAP/FIND, and a run of misses stalls.",
         "test_a_run_of_different_missing_paths_is_a_bounded_stall", None),
    Rule("worker.bounded_no_progress", "worker", "gate",
         "Consecutive turns that change nothing and learn nothing end in a reset, then a stall, whatever the errors.",
         "test_a_cycle_of_different_errors_is_a_bounded_stall", None),
    Rule("worker.no_fenced_writes", "worker", "gate",
         "A WRITE body that opens with a code fence is rejected.",
         "test_fenced_py_write_rejected", None),
    Rule("worker.run_off_by_default", "worker", "gate",
         "RUN is denied unless the host explicitly opts in or supplies an executor.",
         "test_run_is_denied_by_default_without_calling_subprocess", None),
    Rule("worker.one_command_per_response", "worker", "prompt",
         "Every response holds exactly one executable command.",
         "probe:worker_one_command", "exactly one actual executable BID command"),
)
