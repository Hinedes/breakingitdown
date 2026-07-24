import re


_TASK_LINE_RE = re.compile(r'^(\s*[-*]\s+\[)([ x])(\]\s+(T\d+)\b\s*(.*))$')


def _norm(text):
    """Normalize \r\n to \n."""
    return text.replace("\r\n", "\n") if text else text


def parse_todo(text):
    tasks = []
    current_task = None
    for line in _norm(text).split('\n'):
        match = _TASK_LINE_RE.match(line)
        if match:
            if current_task:
                tasks.append(current_task)
            checked = match.group(2) == 'x'
            task_id = match.group(4)
            current_task = {
                "checked": checked,
                "id": task_id,
                "number": int(task_id[1:]),
                "description": match.group(5).strip().lstrip('—–-').strip(),
            }
    if current_task:
        tasks.append(current_task)
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
