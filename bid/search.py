import hashlib
import json
import os
import re
import shutil
import tempfile
import time


# ── Constants ────────────────────────────────────────────────────────

SEARCH_TIME_FORMAT = "%Y-%m-%d %H:%M:%S UTC"
_MAX_RESULTS = 5
_MAX_TITLE = 200
_MAX_URL = 500
_MAX_SUMMARY = 500
_MAX_EXTRACT = 2000
_MAX_PUBLISHED = 100
_CACHE_DIR = ".bid/search_cache"


# ── Query normalization ──────────────────────────────────────────────

def _canonical_query(query):
    q = query.strip().lower()
    q = re.sub(r"\s+", " ", q)
    return q


def _query_hash(query):
    return hashlib.sha256(_canonical_query(query).encode()).hexdigest()


# ── Sanitization ─────────────────────────────────────────────────────

def _sanitize_field(text, max_len):
    """Strip, bound, and remove control characters (including newlines)."""
    if not text:
        return ""
    text = text.strip()
    text = re.sub(r"[\r\n]", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text[:max_len]


def _validate_url(url):
    if not url:
        return ""
    url = url.strip()
    if re.match(r"^https?://", url):
        return url[: _MAX_URL]
    return ""


# ── Persistent search cache (atomic writes) ──────────────────────────

class SearchCache:
    def __init__(self, workspace):
        self._dir = os.path.join(workspace, _CACHE_DIR)
        os.makedirs(self._dir, exist_ok=True)
        self._index_path = os.path.join(self._dir, "index.json")
        self._index = self._load_index()

    def _load_index(self):
        if os.path.exists(self._index_path):
            try:
                with open(self._index_path, encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError):
                pass
        return {}

    def _save_index_atomic(self):
        tmp = self._index_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self._index, f, indent=2)
        os.replace(tmp, self._index_path)

    def get(self, query):
        h = _query_hash(query)
        entry = self._index.get(h)
        if entry is None:
            return None
        # Verify evidence file still exists
        path = entry.get("path", "")
        if not path or not os.path.exists(os.path.join(os.path.dirname(self._index_path),
                                                       "..", "..", path)):
            # Stale entry — clean it
            self._index.pop(h, None)
            self._save_index_atomic()
            return None
        return {"path": path, "retrieved_at": entry.get("retrieved_at", ""),
                "provider": entry.get("provider", "")}

    def put(self, query, results, path, provider_name):
        h = _query_hash(query)
        self._index[h] = {
            "path": path,
            "provider": provider_name,
            "retrieved_at": time.strftime(SEARCH_TIME_FORMAT, time.gmtime()),
            "num_results": len(results),
        }
        self._save_index_atomic()

    def get_stored_results(self, query):
        h = _query_hash(query)
        entry = self._index.get(h)
        return entry


# ── Search providers ─────────────────────────────────────────────────

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
    provider_name = "httpx-web"

    def __init__(self, endpoint=None):
        self._endpoint = endpoint

    def search(self, query, max_results=5):
        import httpx
        params = {"q": query, "max": max_results}
        url = self._endpoint or "https://api.duckduckgo.com/"
        try:
            resp = httpx.get(url, params=params, timeout=15)
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            return [], str(exc)

        results = []
        for item in (data.get("results") or data.get("RelatedTopics") or []):
            if len(results) >= max_results:
                break
            title = item.get("Text", item.get("Title", ""))
            url_raw = item.get("FirstURL", item.get("URL", ""))
            results.append(SearchResult(
                title=title,
                url=url_raw,
                summary=item.get("Abstract", "") or title,
                extract=item.get("AbstractText", ""),
            ))
        return results, None


class MockSearchProvider(SearchProvider):
    provider_name = "mock"

    def __init__(self, responses=None):
        self.responses = responses or {}

    def search(self, query, max_results=5):
        canonical = _canonical_query(query)
        if canonical in self.responses:
            return self.responses[canonical][:max_results], None
        return [
            SearchResult(
                title=f"Mock result for: {query[:60]}",
                url="https://example.com/mock",
                summary="Mock search result.",
                extract="Mock data for testing.",
            )
        ], None


def create_provider(config):
    if os.environ.get("BID_SEARCH_MOCK") == "1":
        return MockSearchProvider()
    endpoint = config.get("search_endpoint")
    return HttpxSearchProvider(endpoint=endpoint)


# ── Result storage ───────────────────────────────────────────────────

_RESEARCH_PREFIX = "docs/research/"


def _research_dir(workspace, task_number):
    return os.path.join(workspace, _RESEARCH_PREFIX, f"T{task_number}")


def _research_rel_dir(task_number):
    return f"{_RESEARCH_PREFIX}T{task_number}"


def _next_search_number(workspace, task_number):
    rdir = _research_dir(workspace, task_number)
    os.makedirs(rdir, exist_ok=True)
    existing = []
    for name in os.listdir(rdir):
        m = re.match(r"search-(\d+)\.md$", name)
        if m:
            existing.append(int(m.group(1)))
    return max(existing, default=0) + 1


def _write_search_result(workspace, task_number, query, results, provider_name):
    """Store all provider fields inside an untrusted-data block."""
    num = _next_search_number(workspace, task_number)
    rdir = _research_dir(workspace, task_number)
    rel_path = os.path.join(_research_rel_dir(task_number), f"search-{num:03d}.md")
    abs_path = os.path.join(workspace, rel_path)
    os.makedirs(os.path.dirname(abs_path), exist_ok=True)
    ts = time.strftime(SEARCH_TIME_FORMAT, time.gmtime())

    lines = [
        "# Search",
        "",
        f"Query: {query}",
        f"Timestamp: {ts}",
        f"Provider: {provider_name}",
        "",
        "## Sources",
        "",
        "> **Source material (untrusted — verify before use)**",
        ">",
    ]
    for i, r in enumerate(results, 1):
        lines.append(f"> ### {i}. {r.title}")
        if r.url:
            lines.append(f"> URL: {r.url}")
        if r.published:
            lines.append(f"> Published: {r.published}")
        lines.append(f"> Retrieved: {ts}")
        if r.summary:
            lines.append(f"> Summary: {r.summary}")
        if r.extract:
            lines.append(f"> ")
            for line in r.extract.split("\n"):
                lines.append(f"> {line}")
        lines.append(">")

    with open(abs_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    return rel_path


# ── Orchestration ────────────────────────────────────────────────────

def execute_search(workspace, task_number, query, cache, provider, max_results=5):
    """Execute or cache-hit a search.

    Returns (rel_path_or_None, num_results, error_str_or_None, is_cache_hit).
    """
    normalized = _canonical_query(query)
    cached = cache.get(query)
    if cached is not None:
        # Return cached path — it has been verified to exist
        return cached["path"], 0, None, True

    try:
        results, err = provider.search(query, max_results=min(max_results, _MAX_RESULTS))
    except Exception as exc:
        return None, 0, str(exc), False

    if err:
        return None, 0, err, False

    if not results:
        return None, 0, "no results returned", False

    # Bound provider output
    results = list(results)[:_MAX_RESULTS]

    rel_path = _write_search_result(workspace, task_number, query, results, provider.provider_name)
    cache.put(query, results, rel_path, provider.provider_name)
    return rel_path, len(results), None, False


# ── Citation enforcement ─────────────────────────────────────────────

_EVIDENCE_PATH_RE = re.compile(r"docs/research/T\d+/search-\d+\.md")
_EVIDENCE_URL_RE = re.compile(r"https?://[^\s\)\"']+")


def has_citations(artifact_text, research_dir):
    """Check if artifact text references a stored evidence path or URL from research."""
    if not artifact_text:
        return False
    # Check for evidence file paths
    if _EVIDENCE_PATH_RE.search(artifact_text):
        return True
    # Check for URLs found in this task's research files
    if not os.path.isdir(research_dir):
        return False
    known_urls = set()
    for fname in os.listdir(research_dir):
        fpath = os.path.join(research_dir, fname)
        if not os.path.isfile(fpath):
            continue
        try:
            with open(fpath, encoding="utf-8") as f:
                content = f.read()
            for m in _EVIDENCE_URL_RE.finditer(content):
                known_urls.add(m.group(0))
        except OSError:
            pass
    for m in _EVIDENCE_URL_RE.finditer(artifact_text):
        if m.group(0) in known_urls:
            return True
    return False
