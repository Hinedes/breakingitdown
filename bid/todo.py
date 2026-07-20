import re


_TASK_LINE_RE = re.compile(r'^(\s*[-*]\s+\[)([ x])(\]\s+T(\d+)\b.*)$')


def parse_todo(text):
    tasks = []
    for line in text.split('\n'):
        m = re.match(r'^\s*[-*]\s+\[([ x])\]\s+(T\d*)\b\s*(.*)', line)
        if m and m.group(2) != 'T':
            checked = m.group(1) == 'x'
            task_id = m.group(2)
            num_str = task_id[1:]
            number = int(num_str) if num_str.isdigit() else 0
            tasks.append({
                "checked": checked,
                "id": task_id,
                "number": number,
                "description": m.group(3).strip().lstrip('—–-').strip(),
            })
    return tasks


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
    old_lines = old_text.split('\n')
    new_lines = new_text.split('\n')
    if len(old_lines) != len(new_lines):
        return False, "worker may not add or remove TODO lines"

    changed = [index for index, pair in enumerate(zip(old_lines, new_lines)) if pair[0] != pair[1]]
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
