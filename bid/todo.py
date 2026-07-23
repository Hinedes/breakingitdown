import re


_TASK_LINE_RE = re.compile(r'^(\s*[-*]\s+\[)([ x])(\]\s+T(\d+)\b.*)$')
_META_RE = re.compile(r'^\s{2,}(Output|Inputs|Accept)\s*:\s*(.*)')
_META_KEY_MAP = {"Output": "output", "Inputs": "inputs", "Accept": "accept"}


def _norm(text):
    """Normalize \r\n to \n."""
    return text.replace("\r\n", "\n") if text else text


def parse_todo(text):
    tasks = []
    current_task = None
    for line in _norm(text).split('\n'):
        match = re.match(r'^\s*[-*]\s+\[([ x])\]\s+(T\d*)\b\s*(.*)', line)
        if match and match.group(2) != 'T':
            if current_task:
                tasks.append(current_task)
            checked = match.group(1) == 'x'
            task_id = match.group(2)
            number_text = task_id[1:]
            current_task = {
                "checked": checked,
                "id": task_id,
                "number": int(number_text) if number_text.isdigit() else 0,
                "description": match.group(3).strip().lstrip('—–-').strip(),
                "has_output": False,
                "has_inputs": False,
                "has_accept": False,
            }
            continue
        meta = _META_RE.match(line)
        if meta and current_task is not None:
            key = _META_KEY_MAP[meta.group(1)]
            current_task[key] = meta.group(2).strip()
            current_task[f"has_{key}"] = True
    if current_task:
        tasks.append(current_task)
    return tasks


def get_task_metadata(tasks, number):
    """Return (output_path, input_paths) for a task, or defaults."""
    task = get_task(tasks, number)
    if not task:
        return f"docs/work/T{number}.md", []
    output = task.get("output", f"docs/work/T{number}.md")
    inputs_raw = task.get("inputs", "")
    inputs = [p.strip() for p in inputs_raw.replace(",", "\n").split("\n") if p.strip()]
    return output, inputs


def get_task_acceptance(tasks, number):
    task = get_task(tasks, number)
    if not task:
        return ""
    return task.get("accept", "")


def get_task(tasks, number):
    for task in tasks:
        if task["number"] == number:
            return task
    return None


def first_unchecked(tasks):
    for task in tasks:
        if not task["checked"]:
            return task
    return None


def all_checked(tasks):
    return bool(tasks) and all(task["checked"] for task in tasks)


def set_task_checked(text, task_number, checked=True):
    lines = text.split('\n')
    tag = f'T{task_number}' if task_number else 'TN'
    for index, line in enumerate(lines):
        match = re.match(r'^(\s*[-*]\s+\[)([ x])(\]\s+' + re.escape(tag) + r'\b.*)$', line)
        if match:
            marker = 'x' if checked else ' '
            lines[index] = match.group(1) + marker + match.group(3)
            break
    return '\n'.join(lines)


def validate_worker_todo_update(old_text, new_text, worker_number):
    """Allow a Worker to change only its own checkbox, in either direction."""
    old_lines = _norm(old_text).splitlines()
    new_lines = _norm(new_text).splitlines()
    if len(old_lines) != len(new_lines):
        return False, "worker may not add or remove TODO lines"

    changed = [
        index
        for index, (old_line, new_line) in enumerate(zip(old_lines, new_lines))
        if old_line != new_line
    ]
    if len(changed) != 1:
        if not changed:
            return False, "TODO write made no checkbox transition"
        return False, "worker may change only one TODO line"

    index = changed[0]
    old_match = _TASK_LINE_RE.match(old_lines[index])
    new_match = _TASK_LINE_RE.match(new_lines[index])
    if not old_match or not new_match:
        return False, "worker may only edit a Markdown task checkbox"

    old_number = int(old_match.group(4))
    new_number = int(new_match.group(4))
    if old_number != worker_number or new_number != worker_number:
        return False, f"Worker {worker_number} may only change T{worker_number}"

    if old_match.group(1) != new_match.group(1) or old_match.group(3) != new_match.group(3):
        return False, "worker may not rewrite the task description"

    if old_match.group(2) == new_match.group(2):
        return False, "checkbox did not change"

    return True, None
