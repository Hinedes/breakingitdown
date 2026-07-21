import hashlib
import json
import os
import re
import tempfile
import time
from contextlib import contextmanager
from urllib.parse import urlsplit


SEARCH_TIME_FORMAT = "%Y-%m-%d %H:%M:%S UTC"
_MAX_RESULTS = 5
_MAX_TITLE = 200
_MAX_URL = 500
_MAX_SUMMARY = 500
_MAX_EXTRACT = 2000
_MAX_PUBLISHED = 100
_CACHE_DIR = ".bid/search_cache"
_CACHE_LOCK_TIMEOUT = 30.0
_CACHE_LOCK_STALE = 120.0
_RESEARCH_PREFIX = "docs/research/"
_EVIDENCE_FILE_RE = re.compile(r"^docs/research/T\d+/search-\d+\.md$")
_EVIDENCE_URL_RE = re.compile(r"https?://[^\s\)\]\}\>\"']+")


def _canonical_query(query):
    query = "" if query is None else str(query)
    return re.sub(r"\s+", " ", query.strip().lower())


def _query_hash(query):
    return hashlib.sha256(_canonical_query(query).encode("utf-8")).hexdigest()


def _sanitize_field(text, max_len):
    if text is None:
        return ""
    text = str(text).strip()
    text = re.sub(r"[\x00-\x1f\x7f]+", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text[:max_len]


def _validate_url(url):
    if url is None:
        return ""
    value = str(url).strip()
    if len(value) > _MAX_URL or re.search(r"\s", value):
        return ""
    try:
        parsed = urlsplit(value)
    except ValueError:
        return ""
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return ""
    return value


def _research_dir(workspace, task_number):
    return os.path.join(workspace, "docs", "research", f"T{task_number}")


def _research_rel_dir(task_number):
    return f"{_RESEARCH_PREFIX}T{task_number}"


def _safe_evidence_path(workspace, rel_path, must_exist=True):
    if not isinstance(rel_path, str):
        return None
    rel_path = rel_path.replace(os.sep, "/")
    if not _EVIDENCE_FILE_RE.fullmatch(rel_path):
        return None
    root = os.path.realpath(workspace)
    absolute = os.path.realpath(os.path.join(root, rel_path))
    if not absolute.startswith(root + os.sep):
        return None
    if must_exist and not os.path.isfile(absolute):
        return None
    return absolute


def _next_search_number_locked(workspace, task_number):
    directory = _research_dir(workspace, task_number)
    os.makedirs(directory, exist_ok=True)
    numbers = []
    for name in os.listdir(directory):
        match = re.fullmatch(r"search-(\d+)\.md", name)
        if match:
            numbers.append(int(match.group(1)))
    return max(numbers, default=0) + 1


def _render_search_result(query, results, provider_name, timestamp):
    provider_name = _sanitize_field(provider_name, 100) or "unknown"
    query = _sanitize_field(query, 500)
    lines = [
        "# Search",
        "",
        f"Query: {query}",
        f"Timestamp: {timestamp}",
        f"Provider: {provider_name}",
        "",
        "## Sources",
        "",
        "> **Source material (untrusted — verify before use)**",
        ">",
    ]
    for index, result in enumerate(results, 1):
        lines.append(f"> ### {index}. {result.title}")
        if result.url:
            lines.append(f"> URL: {result.url}")
        if result.published:
            lines.append(f"> Published: {result.published}")
        lines.append(f"> Retrieved: {timestamp}")
        if result.summary:
            lines.append(f"> Summary: {result.summary}")
        if result.extract:
            lines.append(">")
            lines.append(f"> {result.extract}")
        lines.append(">")
    return "\n".join(lines)


def _write_text_atomic(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".tmp-", dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.remove(temporary)
        except OSError:
            pass
        raise


class SearchCache:
    """Persistent query cache with task-scoped evidence materialization."""

    def __init__(self, workspace):
        self.workspace = os.path.realpath(workspace)
        self._dir = os.path.join(self.workspace, _CACHE_DIR)
        os.makedirs(self._dir, exist_ok=True)
        self._index_path = os.path.join(self._dir, "index.json")
        self._lock_path = os.path.join(self._dir, "lock")
        self._index = self._load_index()

    @contextmanager
    def _lock(self):
        deadline = time.monotonic() + _CACHE_LOCK_TIMEOUT
        while True:
            try:
                descriptor = os.open(self._lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                with os.fdopen(descriptor, "w", encoding="ascii") as handle:
                    handle.write(f"{os.getpid()} {time.time()}\n")
                break
            except FileExistsError:
                try:
                    age = time.time() - os.path.getmtime(self._lock_path)
                    if age > _CACHE_LOCK_STALE:
                        os.remove(self._lock_path)
                        continue
                except OSError:
                    continue
                if time.monotonic() >= deadline:
                    raise RuntimeError("search cache lock timeout")
                time.sleep(0.05)
        try:
            yield
        finally:
            try:
                os.remove(self._lock_path)
            except OSError:
                pass

    def _load_index(self):
        if not os.path.isfile(self._index_path):
            return {}
        try:
            with open(self._index_path, encoding="utf-8") as handle:
                value = json.load(handle)
            return value if isinstance(value, dict) else {}
        except (json.JSONDecodeError, OSError):
            return {}

    def _save_index_atomic(self):
        _write_text_atomic(self._index_path, json.dumps(self._index, indent=2, sort_keys=True))

    def _clean_paths(self, entry):
        paths = entry.get("paths")
        if not isinstance(paths, list):
            legacy = entry.get("path")
            paths = [legacy] if isinstance(legacy, str) else []
        clean = []
        for path in paths:
            if _safe_evidence_path(self.workspace, path, must_exist=True) and path not in clean:
                clean.append(path)
        entry.pop("path", None)
        entry["paths"] = clean
        return clean

    def _entry_for_query_locked(self, query):
        self._index = self._load_index()
        query_hash = _query_hash(query)
        entry = self._index.get(query_hash)
        if not isinstance(entry, dict):
            return query_hash, None
        before = list(entry.get("paths", [])) if isinstance(entry.get("paths"), list) else None
        paths = self._clean_paths(entry)
        if not paths:
            self._index.pop(query_hash, None)
            self._save_index_atomic()
            return query_hash, None
        if before != paths or "path" in entry:
            self._save_index_atomic()
        return query_hash, entry

    def get(self, query, task_number=None):
        with self._lock():
            _, entry = self._entry_for_query_locked(query)
            if entry is None:
                return None
            paths = entry["paths"]
            selected = paths[0]
            if task_number is not None:
                prefix = _research_rel_dir(task_number) + "/"
                selected = next((path for path in paths if path.startswith(prefix)), selected)
            return {
                "path": selected,
                "retrieved_at": entry.get("retrieved_at", ""),
                "provider": entry.get("provider", ""),
                "num_results": int(entry.get("num_results", 0) or 0),
            }

    def _write_evidence_locked(self, task_number, content):
        number = _next_search_number_locked(self.workspace, task_number)
        rel_path = f"{_research_rel_dir(task_number)}/search-{number:03d}.md"
        absolute = _safe_evidence_path(self.workspace, rel_path, must_exist=False)
        if absolute is None:
            raise ValueError("invalid generated evidence path")
        _write_text_atomic(absolute, content)
        return rel_path

    def materialize(self, query, task_number):
        """Return a task-local evidence path for a cached query."""
        with self._lock():
            query_hash, entry = self._entry_for_query_locked(query)
            if entry is None:
                return None
            prefix = _research_rel_dir(task_number) + "/"
            for path in entry["paths"]:
                if path.startswith(prefix):
                    return {
                        "path": path,
                        "provider": entry.get("provider", ""),
                        "retrieved_at": entry.get("retrieved_at", ""),
                        "num_results": int(entry.get("num_results", 0) or 0),
                    }
            source = _safe_evidence_path(self.workspace, entry["paths"][0], must_exist=True)
            if source is None:
                self._index.pop(query_hash, None)
                self._save_index_atomic()
                return None
            with open(source, encoding="utf-8") as handle:
                content = handle.read()
            new_path = self._write_evidence_locked(task_number, content)
            entry["paths"].append(new_path)
            self._save_index_atomic()
            return {
                "path": new_path,
                "provider": entry.get("provider", ""),
                "retrieved_at": entry.get("retrieved_at", ""),
                "num_results": int(entry.get("num_results", 0) or 0),
            }

    def store(self, query, task_number, results, provider_name):
        timestamp = time.strftime(SEARCH_TIME_FORMAT, time.gmtime())
        content = _render_search_result(query, results, provider_name, timestamp)
        with self._lock():
            query_hash, existing = self._entry_for_query_locked(query)
            if existing is not None:
                prefix = _research_rel_dir(task_number) + "/"
                local = next((path for path in existing["paths"] if path.startswith(prefix)), None)
                if local:
                    return local
                source = _safe_evidence_path(self.workspace, existing["paths"][0], must_exist=True)
                if source is not None:
                    with open(source, encoding="utf-8") as handle:
                        content = handle.read()
            rel_path = self._write_evidence_locked(task_number, content)
            if existing is None:
                self._index[query_hash] = {
                    "paths": [rel_path],
                    "provider": _sanitize_field(provider_name, 100) or "unknown",
                    "retrieved_at": timestamp,
                    "num_results": len(results),
                }
            else:
                existing["paths"].append(rel_path)
            self._save_index_atomic()
            return rel_path

    def put(self, query, results, path, provider_name="unknown"):
        """Compatibility API for callers that already wrote an evidence file."""
        if _safe_evidence_path(self.workspace, path, must_exist=True) is None:
            raise ValueError("cache path is not a valid evidence file")
        with self._lock():
            self._index = self._load_index()
            query_hash = _query_hash(query)
            entry = self._index.get(query_hash)
            if not isinstance(entry, dict):
                entry = {
                    "paths": [],
                    "provider": _sanitize_field(provider_name, 100) or "unknown",
                    "retrieved_at": time.strftime(SEARCH_TIME_FORMAT, time.gmtime()),
                    "num_results": len(results),
                }
                self._index[query_hash] = entry
            self._clean_paths(entry)
            if path not in entry["paths"]:
                entry["paths"].append(path)
            self._save_index_atomic()

    def get_stored_results(self, query):
        with self._lock():
            _, entry = self._entry_for_query_locked(query)
            return dict(entry) if entry is not None else None


class SearchResult:
    def __init__(self, title, url, summary, extract="", published=""):
        self.title = _sanitize_field(title, _MAX_TITLE)
        self.url = _validate_url(url)
        self.summary = _sanitize_field(summary, _MAX_SUMMARY)
        self.extract = _sanitize_field(extract, _MAX_EXTRACT)
        self.published = _sanitize_field(published, _MAX_PUBLISHED)


class SearchProvider:
    provider_name = "unknown"

    def search(self, query, max_results=5):
        raise NotImplementedError


class HttpxSearchProvider(SearchProvider):
    provider_name = "duckduckgo-instant-answer"

    def __init__(self, endpoint=None):
        self._endpoint = endpoint or "https://api.duckduckgo.com/"

    @staticmethod
    def _iter_topics(items):
        for item in items or []:
            if not isinstance(item, dict):
                continue
            nested = item.get("Topics")
            if isinstance(nested, list):
                yield from HttpxSearchProvider._iter_topics(nested)
            else:
                yield item

    def search(self, query, max_results=5):
        canonical = _canonical_query(query)
        if not canonical:
            return [], "empty search query"
        import httpx

        params = {
            "q": query,
            "format": "json",
            "no_html": 1,
            "no_redirect": 1,
            "skip_disambig": 1,
        }
        try:
            response = httpx.get(self._endpoint, params=params, timeout=15, follow_redirects=True)
            response.raise_for_status()
            data = response.json()
        except Exception as exc:
            return [], str(exc)
        if not isinstance(data, dict):
            return [], "search endpoint returned a non-object response"

        limit = min(max(int(max_results), 0), _MAX_RESULTS)
        results = []
        abstract = data.get("AbstractText") or data.get("Abstract")
        abstract_url = data.get("AbstractURL")
        if abstract and abstract_url and limit:
            results.append(SearchResult(
                title=data.get("Heading") or query,
                url=abstract_url,
                summary=abstract,
                extract=abstract,
                published=data.get("AbstractSource", ""),
            ))
        raw_items = data.get("results") or data.get("Results") or data.get("RelatedTopics") or []
        for item in self._iter_topics(raw_items):
            if len(results) >= limit:
                break
            title = item.get("Text") or item.get("Title") or ""
            url = item.get("FirstURL") or item.get("URL") or ""
            if not title and not url:
                continue
            results.append(SearchResult(
                title=title or url,
                url=url,
                summary=item.get("Abstract") or title,
                extract=item.get("AbstractText") or title,
                published=item.get("Source") or "",
            ))
        return results[:limit], None


class MockSearchProvider(SearchProvider):
    provider_name = "mock"

    def __init__(self, responses=None):
        self.responses = responses or {}

    def search(self, query, max_results=5):
        canonical = _canonical_query(query)
        if not canonical:
            return [], "empty search query"
        if canonical in self.responses:
            return list(self.responses[canonical])[: min(max_results, _MAX_RESULTS)], None
        return [SearchResult(
            title=f"Mock result for: {query[:60]}",
            url="https://example.com/mock",
            summary="Mock search result.",
            extract="Mock data for testing.",
        )], None


def create_provider(config):
    if os.environ.get("BID_SEARCH_MOCK") == "1":
        return MockSearchProvider()
    return HttpxSearchProvider(endpoint=config.get("search_endpoint") or None)


def execute_search(workspace, task_number, query, cache, provider, max_results=5):
    """Return (task-local evidence path, result count, error, cache hit)."""
    if not _canonical_query(query):
        return None, 0, "empty search query", False
    cached = cache.materialize(query, task_number)
    if cached is not None:
        return cached["path"], cached["num_results"], None, True
    try:
        response = provider.search(query, max_results=min(max_results, _MAX_RESULTS))
    except Exception as exc:
        return None, 0, str(exc), False
    if not isinstance(response, tuple) or len(response) != 2:
        return None, 0, "search provider returned an invalid response", False
    results, error = response
    if error:
        return None, 0, str(error), False
    results = list(results or [])[:_MAX_RESULTS]
    if not results:
        return None, 0, "no results returned", False
    normalized_results = [
        result if isinstance(result, SearchResult) else SearchResult(
            getattr(result, "title", ""),
            getattr(result, "url", ""),
            getattr(result, "summary", ""),
            getattr(result, "extract", ""),
            getattr(result, "published", ""),
        )
        for result in results
    ]
    path = cache.store(query, task_number, normalized_results, provider.provider_name)
    return path, len(normalized_results), None, False


def has_citations(artifact_text, research_dir):
    """Require a citation to an existing file or URL in this task's evidence."""
    if not artifact_text or not os.path.isdir(research_dir):
        return False
    task_name = os.path.basename(os.path.normpath(research_dir))
    known_paths = set()
    known_urls = set()
    for filename in sorted(os.listdir(research_dir)):
        absolute = os.path.join(research_dir, filename)
        if not os.path.isfile(absolute) or not re.fullmatch(r"search-\d+\.md", filename):
            continue
        known_paths.add(f"{_RESEARCH_PREFIX}{task_name}/{filename}")
        try:
            with open(absolute, encoding="utf-8") as handle:
                evidence = handle.read()
        except OSError:
            continue
        for match in _EVIDENCE_URL_RE.finditer(evidence):
            known_urls.add(match.group(0).rstrip(".,;:"))
    if any(path in artifact_text for path in known_paths):
        return True
    artifact_urls = {
        match.group(0).rstrip(".,;:")
        for match in _EVIDENCE_URL_RE.finditer(artifact_text)
    }
    return bool(artifact_urls.intersection(known_urls))
