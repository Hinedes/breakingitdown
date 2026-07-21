import hashlib
import json
import os
import re
import time


# ── Constants ────────────────────────────────────────────────────────

SEARCH_TIME_FORMAT = "%Y-%m-%d %H:%M:%S UTC"
_MAX_RESULTS = 5
_MAX_TITLE = 200
_MAX_URL = 500
_MAX_SUMMARY = 500
_MAX_EXTRACT = 2000
_PROVIDER_NAME = "httpx-web"
_CACHE_DIR = ".bid/search_cache"


# ── Query normalization ──────────────────────────────────────────────

def _canonical_query(query):
    q = query.strip().lower()
    q = re.sub(r"\s+", " ", q)
    return q


def _query_hash(query):
    return hashlib.sha256(_canonical_query(query).encode()).hexdigest()


# ── Persistent search cache ──────────────────────────────────────────

class SearchCache:
    """Persistent disk cache keyed by canonical query hash.

    Stores: query_hash → {"results": [SearchResult dicts], "path": str,
            "provider": str, "retrieved_at": str}
    """

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

    def _save_index(self):
        with open(self._index_path, "w", encoding="utf-8") as f:
            json.dump(self._index, f, indent=2)

    def get(self, query):
        h = _query_hash(query)
        entry = self._index.get(h)
        if entry is None:
            return None
        return {"path": entry["path"], "retrieved_at": entry["retrieved_at"],
                "provider": entry["provider"]}

    def put(self, query, results, path):
        h = _query_hash(query)
        self._index[h] = {
            "results": [{"title": r.title, "url": r.url, "summary": r.summary,
                         "extract": r.extract, "published": r.published} for r in results],
            "path": path,
            "provider": _PROVIDER_NAME,
            "retrieved_at": time.strftime(SEARCH_TIME_FORMAT, time.gmtime()),
        }
        self._save_index()

    def get_stored_results(self, query):
        h = _query_hash(query)
        entry = self._index.get(h)
        if entry is None:
            return None
        return entry.get("results", [])


# ── Search providers ─────────────────────────────────────────────────

class SearchResult:
    def __init__(self, title, url, summary, extract="", published=""):
        self.title = _bound_field(title, _MAX_TITLE)
        self.url = _validate_url(url)
        self.summary = _bound_field(summary, _MAX_SUMMARY)
        self.extract = _bound_field(extract, _MAX_EXTRACT)
        self.published = _bound_field(published, 100)


class SearchProvider:
    def search(self, query, max_results=5):
        raise NotImplementedError


def _bound_field(text, max_len):
    if not text:
        return ""
    return text[:max_len]


def _validate_url(url):
    if not url:
        return ""
    url = url.strip()
    if re.match(r"^https?://", url):
        return url[: _MAX_URL]
    return ""


class HttpxSearchProvider(SearchProvider):
    """Real search provider using httpx to call a search endpoint."""

    def __init__(self, endpoint=None):
        self._endpoint = endpoint

    def search(self, query, max_results=5):
        import httpx
        params = {"q": query, "max": min(max_results, _MAX_RESULTS)}
        url = self._endpoint or "https://api.duckduckgo.com/"
        try:
            resp = httpx.get(url, params=params, timeout=15)
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            return self._fallback_search(query, max_results)

        results = []
        for item in (data.get("results") or data.get("RelatedTopics") or []):
            if len(results) >= min(max_results, _MAX_RESULTS):
                break
            title = item.get("Text", item.get("Title", ""))
            url_raw = item.get("FirstURL", item.get("URL", ""))
            results.append(SearchResult(
                title=title,
                url=url_raw,
                summary=item.get("Abstract", "") or title,
                extract=item.get("AbstractText", ""),
            ))
        return results

    def _fallback_search(self, query, max_results):
        """Fallback: scrape from a text corpus or return empty."""
        return []


class MockSearchProvider(SearchProvider):
    """Returns canned results.  Must be explicitly enabled via env or constructor."""

    def __init__(self, responses=None):
        self.responses = responses or {}

    def search(self, query, max_results=5):
        canonical = _canonical_query(query)
        if canonical in self.responses:
            return self.responses[canonical][:min(max_results, _MAX_RESULTS)]
        return [
            SearchResult(
                title=f"Mock result for: {query[:60]}",
                url="https://example.com/mock",
                summary=f"Mock search result.",
                extract="This is a mock search result for testing purposes only.",
            )
        ]


def create_provider(config):
    """Create a SearchProvider based on config.  Mock only when BID_SEARCH_MOCK=1."""
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
    """Store search results with provenance.  Returns relative path."""
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
    ]
    for i, r in enumerate(results, 1):
        lines.append(f"### {i}. {r.title}")
        if r.url:
            lines.append(f"URL: {r.url}")
        if r.published:
            lines.append(f"Published: {r.published}")
        lines.append(f"Retrieved: {ts}")
        if r.summary:
            lines.append(f"Summary: {r.summary}")
        if r.extract:
            lines.append("")  # blank line before untrusted block
            lines.append("> **Source material** (untrusted, verify before use)")
            lines.append(">")
            for line in r.extract.split("\n"):
                lines.append(f"> {line}")
        lines.append("")

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
        return cached["path"], cached.get("_num", 1), None, True

    try:
        results = provider.search(query, max_results=min(max_results, _MAX_RESULTS))
    except Exception as exc:
        return None, 0, str(exc), False

    if not results:
        return None, 0, "no results returned", False

    rel_path = _write_search_result(workspace, task_number, query, results, provider_name=_PROVIDER_NAME)
    cache.put(query, results, rel_path)
    return rel_path, len(results), None, False
