PLAN_GRAMMAR = r'''
root ::= item ("\n" item)* "\n"?
item ::= "- [ ] " text
text ::= [^\n]+
'''

WORKER_GRAMMAR = r'''
root ::= command "\n"?
command ::= read | search | run | write | replace | "Done"
read ::= "READ " path
search ::= "SEARCH " line
run ::= "RUN " line
write ::= "WRITE " path "\n" body "END WRITE"
body ::= (bodyline "\n")*
replace ::= "REPLACE " path "\n" line ("\n" bodyline)* "\n---REPLACE_WITH---\n" newtext "\nEND REPLACE"
newtext ::= (line ("\n" bodyline)*)?
path ::= [^\n]+
bodyline ::= [^\n]*
line ::= [^\n]+
'''


def _worker_grammar(config):
    commands = ["read"]
    if config.get("max_searches_per_worker", 10) > 0:
        commands.append("search")
    if config.get("allow_host_run", False) or callable(config.get("run_executor")):
        commands.append("run")
    commands.extend(("write", "replace", '"Done"'))
    return WORKER_GRAMMAR.replace(
        "command ::= read | search | run | write | replace | \"Done\"",
        "command ::= " + " | ".join(commands),
    )

REVIEW_GRAMMAR = r'''
root ::= verdict "\nReason: " reason "\n"?
verdict ::= "ACCEPT" | "REWORK"
reason ::= [^\n]+
'''

RECONCILIATION_GRAMMAR = r'''
root ::= section* "# Project\n" project "\n"?
section ::= done | rework | add | replace
done ::= "# Done\n" task ("\n" task)* "\n"
rework ::= "# Rework\n" reworkitem "\n"
add ::= "# Add\n" item ("\n" item)* "\n"
replace ::= "# Replace Remaining Plan\n" replacement ("\n" replacement)* "\n"
task ::= "- T" number
reworkitem ::= "- T" number " - " line
replacement ::= "- [ ] " line
item ::= "- " line
number ::= [0-9]+
project ::= "CONTINUE" | "COMPLETE"
line ::= [^\n]+
'''

def grammar_for(config, role):
    if not config.get("protocol_grammar", False):
        return None
    if role == "worker":
        return _worker_grammar(config)
    return {
        "manager": PLAN_GRAMMAR,
        "worker": WORKER_GRAMMAR,
        "reviewer": REVIEW_GRAMMAR,
        "manager_reconcile": RECONCILIATION_GRAMMAR,
    }[role]
