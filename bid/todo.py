import re

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
    for t in tasks:
        if t["number"] == number:
            return t
    return None


def first_unchecked(tasks):
    for t in tasks:
        if not t["checked"]:
            return t
    return None


def all_checked(tasks):
    return all(t["checked"] for t in tasks)


def set_task_checked(text, task_number, checked=True):
    lines = text.split('\n')
    tag = f'T{task_number}' if task_number else 'TN'
    for i, line in enumerate(lines):
        m = re.match(r'^(\s*[-*]\s+\[)([ x])(\]\s+' + re.escape(tag) + r'\b)', line)
        if m:
            marker = 'x' if checked else ' '
            lines[i] = m.group(1) + marker + m.group(3)
            break
    return '\n'.join(lines)
