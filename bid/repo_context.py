"""Deterministic, local-only repository context for Workers.

This module deliberately stays at file level.  It does not infer project
structure and it never follows symlinks while walking the workspace.
"""

import hashlib
import json
import os
import posixpath
import stat
import tempfile

from . import permissions


INDEX_SCHEMA_VERSION = 1
EXTRACTOR_VERSION = "bid-repo-context-text-v1"
INDEX_REL_PATH = ".bid/repo_context/index.json"
DEFAULT_IGNORES = frozenset({
    ".bid",
    ".git",
    ".hg",
    ".svn",
    "__pycache__",
    ".pytest_cache",
    "node_modules",
    ".venv",
    "venv",
})
DEFAULT_MAX_CHARS = 12000
DEFAULT_MAX_FIND_HITS = 100
DEFAULT_MAX_FILE_BYTES = 1048576
DEFAULT_FIND_CLOSURE_MAX_CHARS = 8000
MAX_EXCERPT_CHARS = 240
REPO_CONTEXT_MODES = frozenset({"off", "tools", "inject"})


class RepositoryContextError(RuntimeError):
    """Raised when an index cannot be refreshed or used."""


def normalize_mode(value):
    """Normalize BID_REPO_CONTEXT and reject unknown non-empty values."""
    raw = "" if value is None else str(value).strip().lower()
    if raw in ("", "0", "off"):
        return "off"
    if raw == "tools":
        return "tools"
    if raw in ("1", "inject"):
        return "inject"
    raise ValueError(
        "BID_REPO_CONTEXT must be one of: 0, off, tools, inject, or 1"
    )


def repo_context_tools_enabled(mode):
    return normalize_mode(mode) in {"tools", "inject"}


def repo_context_injection_enabled(mode):
    return normalize_mode(mode) == "inject"


def _empty_index(max_file_bytes=None):
    return {
        "schema_version": INDEX_SCHEMA_VERSION,
        "extractor_version": EXTRACTOR_VERSION,
        "entries": {},
        **({"max_file_bytes": max_file_bytes} if max_file_bytes is not None else {}),
    }


def _workspace_root(workspace):
    return os.path.realpath(os.path.abspath(os.fspath(workspace)))


def _relative_path(path):
    """Return a normalized repository-relative POSIX path or None."""
    if not isinstance(path, str) or not path or path.startswith("/"):
        return None
    if os.sep == "\\":
        path = path.replace("\\", "/")
    normalized = posixpath.normpath(path)
    if normalized in ("", ".") or normalized == ".." or normalized.startswith("../"):
        return None
    return normalized


def _control_exclusions():
    return set(getattr(permissions, "REPO_CONTEXT_CONTROL_ROOTS", ())) | set(
        getattr(permissions, "CONTROL_ROOTS", ())
    ) | set(
        getattr(permissions, "CONTROL_FILES", ())
    )


def is_excluded_path(relative_path, extra_ignores=()):
    """Return whether a repository-relative path is outside the index."""
    rel = _relative_path(relative_path)
    if rel is None:
        return True
    parts = rel.split("/")
    extra_ignores = tuple(extra_ignores)
    ignored = DEFAULT_IGNORES | {
        str(item).strip("/") for item in extra_ignores if str(item).strip("/")
    }
    explicit = {
        str(item).strip("/").replace("\\", "/")
        for item in extra_ignores
        if str(item).strip("/")
    }
    if rel in explicit or any(rel.startswith(item + "/") for item in explicit if "/" in item):
        return True
    for part in parts:
        if part in ignored:
            return True
    for root in _control_exclusions():
        control = _relative_path(root)
        if control and (rel == control or rel.startswith(control + "/")):
            return True
    return False


def _metadata(st_result):
    return {
        "size": int(st_result.st_size),
        "mtime_ns": int(st_result.st_mtime_ns),
        "ctime_ns": int(st_result.st_ctime_ns),
        "mode": int(stat.S_IMODE(st_result.st_mode)),
        "inode": int(st_result.st_ino),
        "device": int(st_result.st_dev),
    }


def _entry(relative_path, entry_type, st_result=None, classification=None):
    data = {
        "path": relative_path,
        "type": entry_type,
        "classification": classification or entry_type,
    }
    if st_result is not None:
        data.update(_metadata(st_result))
    else:
        data.update({
            "size": None,
            "mtime_ns": None,
            "ctime_ns": None,
            "mode": None,
            "inode": None,
            "device": None,
        })
    return data


def walk_workspace(workspace, extra_ignores=()):
    """Walk workspace entries in stable order without following symlinks."""
    root = _workspace_root(workspace)
    if not os.path.isdir(root):
        raise RepositoryContextError("workspace does not exist or is not a directory")

    entries = []

    def visit(directory, relative_directory, directory_entry=None):
        try:
            with os.scandir(directory) as iterator:
                children = sorted(iterator, key=lambda item: item.name)
        except OSError:
            if directory_entry is not None:
                directory_entry["classification"] = "unreadable"
            return

        for child in children:
            relative = child.name if relative_directory == "." else f"{relative_directory}/{child.name}"
            if is_excluded_path(relative, extra_ignores):
                continue

            try:
                if child.is_symlink():
                    entries.append(_entry(relative, "symlink", child.stat(follow_symlinks=False), "symlink"))
                elif child.is_dir(follow_symlinks=False):
                    item = _entry(relative, "directory", child.stat(follow_symlinks=False), "directory")
                    entries.append(item)
                    visit(child.path, relative, item)
                elif child.is_file(follow_symlinks=False):
                    entries.append(_entry(relative, "file", child.stat(follow_symlinks=False), "file"))
                else:
                    entries.append(_entry(relative, "special", child.stat(follow_symlinks=False), "special"))
            except OSError:
                entries.append(_entry(relative, "file", None, "unreadable"))

    visit(root, ".")
    return entries


def _index_path(workspace):
    return os.path.join(_workspace_root(workspace), INDEX_REL_PATH)


def _index_directory(workspace, create=False):
    root = _workspace_root(workspace)
    bid_directory = os.path.join(root, ".bid")
    context_directory = os.path.join(bid_directory, "repo_context")
    for directory in (bid_directory, context_directory):
        if os.path.lexists(directory):
            if os.path.islink(directory):
                raise RepositoryContextError(
                    "repository context control path must not be a symlink"
                )
            if not os.path.isdir(directory):
                raise RepositoryContextError(
                    "repository context control path must be a directory"
                )
        elif create:
            os.makedirs(directory, exist_ok=True)
    return context_directory


def _valid_entry(path, entry):
    if not isinstance(entry, dict) or entry.get("path") != path:
        return False
    if _relative_path(path) != path:
        return False
    if entry.get("type") not in {"file", "directory", "symlink", "special"}:
        return False
    if not isinstance(entry.get("classification"), str):
        return False
    allowed_classifications = {
        "text", "binary", "oversized", "unreadable"
    } if entry.get("type") == "file" else {
        "directory", "symlink", "special", "unreadable"
    }
    if entry.get("classification") not in allowed_classifications:
        return False
    for field in ("size", "mtime_ns", "ctime_ns", "mode", "inode", "device"):
        value = entry.get(field)
        if not isinstance(value, int) and not (
            entry.get("type") == "file"
            and entry.get("classification") == "unreadable"
            and value is None
        ):
            return False
    if entry.get("type") == "file" and entry.get("classification") == "text":
        if not isinstance(entry.get("sha256"), str) or not isinstance(entry.get("text"), str):
            return False
        try:
            text_hash = hashlib.sha256(entry["text"].encode("utf-8")).hexdigest()
        except UnicodeEncodeError:
            return False
        if text_hash != entry["sha256"]:
            return False
    return True


def load_index(workspace):
    """Load a usable index, returning None for corrupt or unknown data."""
    try:
        directory = _index_directory(workspace)
        path = os.path.join(directory, "index.json")
        if os.path.islink(path):
            return None
        with open(path, encoding="utf-8") as file:
            data = json.load(file)
    except (OSError, json.JSONDecodeError, UnicodeDecodeError, RepositoryContextError):
        return None

    if not isinstance(data, dict):
        return None
    if data.get("schema_version") != INDEX_SCHEMA_VERSION:
        return None
    if data.get("extractor_version") != EXTRACTOR_VERSION:
        return None
    if "max_file_bytes" in data and (
        not isinstance(data["max_file_bytes"], int) or data["max_file_bytes"] < 0
    ):
        return None
    entries = data.get("entries")
    if not isinstance(entries, dict):
        return None
    for path, entry in entries.items():
        if not _valid_entry(path, entry) or is_excluded_path(path):
            return None
    return {
        "schema_version": INDEX_SCHEMA_VERSION,
        "extractor_version": EXTRACTOR_VERSION,
        "entries": {path: entries[path] for path in sorted(entries)},
        **({"max_file_bytes": data["max_file_bytes"]} if isinstance(data.get("max_file_bytes"), int) else {}),
    }


def _write_index_atomic(workspace, index):
    directory = _index_directory(workspace, create=True)
    path = os.path.join(directory, "index.json")
    temporary = None
    try:
        fd, temporary = tempfile.mkstemp(prefix="index.", suffix=".tmp", dir=directory)
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as file:
            json.dump(index, file, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary:
            try:
                os.unlink(temporary)
            except OSError:
                pass


def _metadata_matches(old, current):
    fields = ("type", "size", "mtime_ns", "ctime_ns", "mode", "inode", "device")
    return all(old.get(field) == current.get(field) for field in fields)


def _cached_entry_usable(entry):
    if entry.get("type") != "file":
        return True
    classification = entry.get("classification")
    if classification == "text":
        return isinstance(entry.get("sha256"), str) and isinstance(entry.get("text"), str)
    return classification in {"binary", "oversized", "unreadable"}


def _within_workspace(path, root):
    candidate = os.path.realpath(path)
    return candidate == root or candidate.startswith(root + os.sep)


def _classify_file(root, entry, max_file_bytes):
    result = dict(entry)
    result.pop("sha256", None)
    result.pop("text", None)

    path = os.path.join(root, entry["path"].replace("/", os.sep))
    if not _within_workspace(path, root):
        result["classification"] = "unreadable"
        return result
    if entry.get("size") is None:
        result["classification"] = "unreadable"
        return result
    if entry["size"] > max_file_bytes:
        result["classification"] = "oversized"
        return result

    try:
        with open(path, "rb") as file:
            content = file.read(max_file_bytes + 1)
    except (OSError, ValueError):
        result["classification"] = "unreadable"
        return result

    if len(content) > max_file_bytes:
        result["classification"] = "oversized"
        return result
    if b"\x00" in content:
        result["classification"] = "binary"
        return result
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        result["classification"] = "binary"
        return result

    result["classification"] = "text"
    result["sha256"] = hashlib.sha256(content).hexdigest()
    result["text"] = text
    return result


def refresh_index(workspace, max_file_bytes=DEFAULT_MAX_FILE_BYTES, extra_ignores=()):
    """Refresh the workspace index and return deterministic refresh statistics."""
    try:
        max_file_bytes = max(0, int(max_file_bytes))
    except (TypeError, ValueError):
        raise RepositoryContextError("max file bytes must be an integer")

    root = _workspace_root(workspace)
    previous = load_index(root)
    old_entries = previous["entries"] if previous else {}
    cache_settings_match = previous is not None and previous.get("max_file_bytes") == max_file_bytes
    walked = walk_workspace(root, extra_ignores)
    current = {}
    stats = {
        "added": 0,
        "changed": 0,
        "removed": 0,
        "reused": 0,
        "unreadable": 0,
        "binary": 0,
        "oversized": 0,
    }

    for walked_entry in walked:
        path = walked_entry["path"]
        old = old_entries.get(path)
        if old and cache_settings_match and _metadata_matches(old, walked_entry) and _cached_entry_usable(old):
            current[path] = dict(old)
            if walked_entry["type"] == "file":
                stats["reused"] += 1
            continue

        if walked_entry["type"] == "file":
            current[path] = _classify_file(root, walked_entry, max_file_bytes)
            if old:
                stats["changed"] += 1
            else:
                stats["added"] += 1
        else:
            current[path] = dict(walked_entry)

    for path, old in old_entries.items():
        if path not in current and old.get("type") == "file":
            stats["removed"] += 1

    for entry in current.values():
        if entry.get("type") != "file":
            continue
        classification = entry.get("classification")
        if classification in ("unreadable", "binary", "oversized"):
            stats[classification] += 1

    index = _empty_index(max_file_bytes)
    index["entries"] = {path: current[path] for path in sorted(current)}
    try:
        _write_index_atomic(root, index)
    except OSError as exc:
        raise RepositoryContextError(f"could not write repository context index: {exc}") from exc
    return stats


def _entries_for(index):
    if isinstance(index, dict) and isinstance(index.get("entries"), dict):
        return index["entries"]
    if isinstance(index, dict) and isinstance(index.get("files"), dict):
        return index["files"]
    return index if isinstance(index, dict) else {}


def search_state_fingerprint(index):
    """Hash only the deterministic state used by literal search."""
    searchable_state = [
        (path, entry.get("classification"), entry.get("sha256"))
        for path, entry in sorted(_entries_for(index).items())
        if entry.get("type") == "file"
    ]
    payload = json.dumps(searchable_state, ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _display_scope(scope):
    if scope in (None, "", "."):
        return None
    return _relative_path(scope) or "__invalid_scope__"


def _is_truncated(output):
    return (
        "... [omitted" in output
        or "[omitted]" in output
        or "[...]" in output
    )


def _display_entry(path, entry):
    entry_type = entry.get("type")
    classification = entry.get("classification")
    display_path = json.dumps(path, ensure_ascii=True)[1:-1]
    if entry_type == "directory":
        display_path += "/"
    if classification in {"binary", "oversized", "unreadable", "symlink", "special"}:
        display_path += f" [{classification}]"
    return display_path


def _omission_line(omitted, max_chars):
    if omitted <= 0:
        return ""
    marker = f"... [omitted {omitted} entries]"
    if len(marker) <= max_chars:
        return marker
    for compact in ("... [omitted]", "[...]", "..."):
        if len(compact) <= max_chars:
            return compact
    return "" if max_chars == 0 else "." * max_chars


def render_workspace_map(index, scope=None, max_chars=DEFAULT_MAX_CHARS):
    """Render a bounded deterministic map without cutting path lines."""
    if isinstance(scope, int) and max_chars == DEFAULT_MAX_CHARS:
        max_chars, scope = scope, None
    try:
        max_chars = max(0, int(max_chars))
    except (TypeError, ValueError):
        max_chars = DEFAULT_MAX_CHARS

    normalized_scope = _display_scope(scope)
    entries = []
    for path, entry in _entries_for(index).items():
        normalized = _relative_path(path)
        if normalized is None or is_excluded_path(normalized):
            continue
        if normalized_scope and not (
            normalized == normalized_scope or normalized.startswith(normalized_scope + "/")
        ):
            continue
        entries.append((normalized, entry))
    entries.sort(key=lambda item: item[0])

    rendered = [_display_entry(path, entry) for path, entry in entries]
    if not rendered:
        return "(empty)"[:max_chars] if max_chars else ""

    output = []
    used = 0
    for index_number, line in enumerate(rendered):
        line_size = len(line) + (1 if output else 0)
        remaining = len(rendered) - index_number - 1
        marker = _omission_line(remaining, max_chars)
        if used + line_size + (len(marker) + 1 if remaining else 0) <= max_chars:
            output.append(line)
            used += line_size
            continue
        if marker:
            while output and used + len(marker) + 1 > max_chars:
                removed = output.pop()
                used -= len(removed) + (1 if output else 0)
            if used:
                output.append(marker)
            else:
                output = [marker[:max_chars]] if max_chars else []
        break

    if len(output) == len(rendered):
        return "\n".join(output)
    if output and output[-1] != _omission_line(len(rendered) - len(output), max_chars):
        omitted = len(rendered) - len(output)
        marker = _omission_line(omitted, max_chars)
        if marker and len("\n".join(output + [marker])) <= max_chars:
            output.append(marker)
    return "\n".join(output)[:max_chars]


def _find_offsets(text, query):
    start = 0
    while True:
        found = text.find(query, start)
        if found < 0:
            return
        yield found
        start = found + len(query)


def _excerpt(text, offset):
    start = text.rfind("\n", 0, offset) + 1
    end = text.find("\n", offset)
    if end < 0:
        end = len(text)
    line = text[start:end].replace("\r", "").replace("\t", " ")
    if len(line) > MAX_EXCERPT_CHARS:
        line = line[: MAX_EXCERPT_CHARS - 3] + "..."
    return text.count("\n", 0, offset) + 1, line


def search_index(index, query, max_hits=DEFAULT_MAX_FIND_HITS):
    """Search cached readable text using a fixed, non-overlapping literal."""
    if not isinstance(query, str) or not query:
        return {
            "ok": False,
            "error": "FIND query must not be empty",
            "output": "error: FIND query must not be empty",
            "hits": [],
            "total_matches": 0,
            "truncated": False,
            "skipped": {"binary": 0, "oversized": 0, "unreadable": 0},
        }
    try:
        max_hits = max(0, int(max_hits))
    except (TypeError, ValueError):
        max_hits = DEFAULT_MAX_FIND_HITS

    skipped = {"binary": 0, "oversized": 0, "unreadable": 0}
    hits = []
    total = 0
    entries = _entries_for(index)
    for path in sorted(entries):
        entry = entries[path]
        if _relative_path(path) != path or is_excluded_path(path):
            continue
        if entry.get("type") != "file":
            continue
        classification = entry.get("classification")
        if classification in skipped:
            skipped[classification] += 1
        if classification != "text" or not isinstance(entry.get("text"), str):
            continue
        for offset in _find_offsets(entry["text"], query):
            total += 1
            if len(hits) < max_hits:
                line, excerpt = _excerpt(entry["text"], offset)
                hits.append({"path": path, "line": line, "excerpt": excerpt})

    truncated = total > len(hits)
    lines = [
        "FIND is exhaustive only over readable indexed text files.",
        f"matches: {total}",
        f"shown: {len(hits)}",
    ]
    for hit in hits:
        display_path = json.dumps(hit["path"], ensure_ascii=True)[1:-1]
        lines.append(f"{display_path}:{hit['line']}: {hit['excerpt']}")
    if truncated:
        lines.append(f"omitted matches: {total - len(hits)}")
    if total == 0:
        lines.append("no matches")
    lines.append(
        "skipped: "
        f"binary={skipped['binary']}, oversized={skipped['oversized']}, "
        f"unreadable={skipped['unreadable']}"
    )
    return {
        "ok": True,
        "error": None,
        "output": "\n".join(lines),
        "hits": hits,
        "total_matches": total,
        "truncated": truncated,
        "skipped": skipped,
    }


class RepositoryContext:
    """Workspace index and bounded read-only context operations."""

    def __init__(
        self,
        workspace,
        max_chars=DEFAULT_MAX_CHARS,
        max_find_hits=DEFAULT_MAX_FIND_HITS,
        max_file_bytes=DEFAULT_MAX_FILE_BYTES,
        extra_ignores=(),
    ):
        self.workspace = _workspace_root(workspace)
        self.max_chars = max_chars
        self.max_find_hits = max_find_hits
        self.max_file_bytes = max_file_bytes
        self.extra_ignores = tuple(extra_ignores)
        self.index = load_index(self.workspace) or _empty_index()

    @property
    def index_path(self):
        return _index_path(self.workspace)

    def refresh(self):
        stats = refresh_index(self.workspace, self.max_file_bytes, self.extra_ignores)
        self.index = load_index(self.workspace) or _empty_index()
        return stats

    def map_result(self, scope=None, refresh=True):
        if refresh:
            self.refresh()
        output = render_workspace_map(self.index, scope=scope, max_chars=self.max_chars)
        entries = [
            path for path in _entries_for(self.index)
            if not is_excluded_path(path)
            and (scope in (None, "", ".") or path == scope or path.startswith(str(scope).rstrip("/") + "/"))
        ]
        return {
            "output": output,
            "entry_count": len(entries),
            "truncated": _is_truncated(output),
            "scope": scope or ".",
        }

    def map(self, scope=None, refresh=True):
        return self.map_result(scope, refresh=refresh)["output"]

    def find_result(self, query, refresh=True):
        if refresh:
            self.refresh()
        return search_index(self.index, query, self.max_find_hits)

    def find(self, query, refresh=True):
        return self.find_result(query, refresh=refresh)["output"]


# Short aliases keep the pure operations easy to discover for callers.
RepoContext = RepositoryContext
workspace_map = render_workspace_map
literal_search = search_index
