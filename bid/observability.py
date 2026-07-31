"""Append-only structured event logging for BID timing observability.

Records UTC timestamps and monotonic durations for model requests, parsed
commands, RUN executions, Worker sessions, Reviewer verdicts and lifecycle
transitions. Timing data is written to .bid/events.jsonl and is never
exposed to Manager, Worker, Task Reviewer or Completion Reviewer prompts.
"""

import json
import os
import time
from datetime import datetime, timezone


def utc_now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class EventLog:
    def __init__(self, path):
        self.path = path
        self._start = time.monotonic()

    @classmethod
    def for_workspace(cls, workspace):
        # Event log lives OUTSIDE the workspace: .bid is a protected control
        # path, and writing there during a RUN would trip the control-state
        # guard. A sibling directory is preserved alongside the run.
        abs_ws = os.path.abspath(workspace).rstrip(os.sep)
        events_dir = os.path.join(os.path.dirname(abs_ws), os.path.basename(abs_ws) + "-events")
        os.makedirs(events_dir, exist_ok=True)
        return cls(os.path.join(events_dir, "events.jsonl"))

    def event(self, kind, **fields):
        entry = {
            "ts_utc": utc_now_iso(),
            "elapsed_s": round(time.monotonic() - self._start, 3),
            "event": kind,
        }
        entry.update(fields)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
        return entry

    def start(self, kind, **fields):
        self.event(kind, phase="start", **fields)
        return {
            "kind": kind,
            "start": time.monotonic(),
            "fields": dict(fields),
        }

    def end(self, token, **fields):
        duration = round(time.monotonic() - token["start"], 3)
        merged = dict(token["fields"])
        merged.update(fields)
        return self.event(token["kind"], phase="end", duration_s=duration, **merged)


_logs = {}


def get_log(workspace):
    if workspace not in _logs:
        _logs[workspace] = EventLog.for_workspace(workspace)
    return _logs[workspace]
