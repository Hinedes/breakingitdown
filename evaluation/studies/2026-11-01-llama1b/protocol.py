"""Explicit exploratory constrained-decoding treatment, not the default BID protocol."""

import json
import re

WORKER_POLICY = """You are BID Worker. Complete the assigned task using workspace files.
Return only executable commands. Never explain or use Markdown fences.
You may WRITE several files and finish with Done in the same response.
Once the requested files have the correct contents, submit Done. Do not keep editing a completed task.

READ filename
  Read an existing file. Do not add an END READ line.

WRITE filename
raw complete file contents
END WRITE
  Create or replace a file. For a final newline, include a blank line before END WRITE.

RUN program arguments
  Execute inside the disposable project sandbox. Python and ordinary Linux commands are available.
  The network, host home, and BID control files are unavailable.
  Do not try to execute a plain text or data file.

Done
  Submit only after the requested changes are written. No END Done line.

Example of a complete task creating notes.txt with the text Hello:
WRITE notes.txt
Hello
END WRITE
Done

Use the actual filename and contents from your assigned task, not the example.
"""

WORKER_GRAMMAR = r'''
root ::= ("Done" | "READ " path | "RUN " argument | write ("\n" write)* ("\nDone")?) "\n"?
write ::= "WRITE " path "\n" body "END WRITE"
path ::= [a-zA-Z0-9_./-]+
argument ::= [^\n]+
body ::= ([^\n]* "\n")*
'''

PLAN_GRAMMAR = r'''
root ::= item ("\n" item)* "\n"?
item ::= "- [ ] " [^\n]+
'''

REVIEW_GRAMMAR = r'''
root ::= ("ACCEPT" | "REWORK") "\nReason: " [^\n]{1,512} "\n"?
'''

REVIEW_POLICY = """You are BID Task Reviewer. Check the candidate diff against the assigned task.
ACCEPT only when the actual file contents satisfy the task. Compare literal text exactly,
including case and escapes. A claimed result is not evidence. Return REWORK for a concrete defect.
The [bytes=N;end=...] metadata describes the actual candidate UTF-8 file and its final newline.
Do not reject solely for work belonging to later checklist tasks.
Return ACCEPT or REWORK, then a newline and Reason: followed by the specific reason.
"""

RECONCILE_POLICY = """You are BID Manager performing project reconciliation.
Compare the original request, current checklist and provisional artifact evidence.
Ratify correct provisional tasks with Done. Request Rework for a concrete unmet requirement.
If every requirement is satisfied and no unchecked work remains, declare COMPLETE.
If unchecked work remains, declare CONTINUE. Add tasks only for an actual missing requirement;
do not invent extra work or copy placeholder examples. Task IDs must come from the current checklist.
The [bytes=N;end=...] metadata gives exact candidate UTF-8 byte lengths and final-newline state; end=none means no final newline. Display separators are not file contents.
Do not add verification-only tasks for an artifact that already satisfies its requirement.
"""


def reconciliation_grammar(messages):
    prompt = messages[1]["content"]
    checklist = prompt.split("Current checklist:\n", 1)[1].split("\n\nProvisional submissions:", 1)[0]
    tasks = re.findall(r"^\s*- \[([ x-])\] T(\d+)\b", checklist, re.MULTILINE)
    active = sorted(int(number) for state, number in tasks if state == "-")
    unchecked = any(state == " " for state, _ in tasks)

    def literal(text):
        return json.dumps(text)

    def done(numbers):
        return "# Done\n" + "".join(f"- T{number}\n" for number in numbers) if numbers else ""

    prefix = literal(done(active))
    project = "CONTINUE" if unchecked else "COMPLETE"
    choices = [f'{prefix} {literal("# Project" + chr(10) + project)}']
    choices.append(f'{prefix} add {literal("# Project" + chr(10) + "CONTINUE")}')
    if unchecked:
        choices.append(f'{prefix} replace {literal("# Project" + chr(10) + "CONTINUE")}')
    for number in active:
        header = done([other for other in active if other < number]) + f"# Rework\n- T{number} - "
        choices.append(f'{literal(header)} text "\\n" add? {literal("# Project" + chr(10) + "CONTINUE")}')
    return 'root ::= (' + " | ".join(choices) + ') "\\n"?\n' + r'''
text ::= [^\n]{1,512}
entry ::= "- " text "\n"
add ::= "# Add\n" entry entry? entry? entry?
replacement ::= "- [ ] " text "\n"
replace ::= "# Replace Remaining Plan\n" replacement replacement? replacement? replacement?
'''


def grammar_for(messages):
    system = messages[0]["content"].lower()
    if "task reviewer" in system:
        return REVIEW_GRAMMAR
    if "project reconciliation" in system:
        return reconciliation_grammar(messages)
    if "bid manager" in system:
        return PLAN_GRAMMAR
    return WORKER_GRAMMAR
