import hashlib
import os
import re
import time


# ── Query normalization ──────────────────────────────────────────────

def _canonical_query(query):
    q = query.strip().lower()
    q = re.sub(r"\s+", " ", q)
    return q


def _query_hash(query):
    return hashlib.sha256(_canonical_query(query).encode()).hexdigest()


# ── Search cache ─────────────────────────────────────────────────────

class SearchCache:
    """In-memory cache keyed by canonical query hash."""

    def __init__(self):
        self._data = {}

    def get(self, query):
        return self._data.get(_query_hash(query))

    def put(self, query, results):
        self._data[_query_hash(query)] = results


# ── Search providers ─────────────────────────────────────────────────

SEARCH_TIME_FORMAT = "%Y-%m-%d %H:%M:%S UTC"


class SearchResult:
    """A single search result with provenance."""

    def __init__(self, title, url, summary, extract="", published=""):
        self.title = title
        self.url = url
        self.summary = summary
        self.extract = extract
        self.published = published


class SearchProvider:
    def search(self, query, max_results=5):
        raise NotImplementedError


class MockSearchProvider(SearchProvider):
    """Returns canned results for testing."""

    def __init__(self, responses=None):
        self.responses = responses or {}  # canonical_query → [SearchResult, ...]

    def search(self, query, max_results=5):
        canonical = _canonical_query(query)
        if canonical in self.responses:
            return self.responses[canonical][:max_results]
        # Default fallback
        return [
            SearchResult(
                title=f"Result for: {query[:60]}",
                url=f"https://example.com/search?q={query[:60]}",
                summary=f"Mock result for query '{query[:80]}'.",
                extract="This is a mock search result for testing purposes.",
            )
        ]


# ── Result storage ───────────────────────────────────────────────────

def _research_dir(workspace, task_number):
    return os.path.join(workspace, "docs", "research", f"T{task_number}")


def _next_search_number(workspace, task_number):
    rdir = _research_dir(workspace, task_number)
    os.makedirs(rdir, exist_ok=True)
    existing = []
    for name in os.listdir(rdir):
        m = re.match(r"search-(\d+)\.md$", name)
        if m:
            existing.append(int(m.group(1)))
    return max(existing, default=0) + 1


def _write_search_result(workspace, task_number, query, results, provider_name="mock"):
    """Store search results to docs/research/TN/search-NNN.md and return the path."""
    num = _next_search_number(workspace, task_number)
    rdir = _research_dir(workspace, task_number)
    path = os.path.join(rdir, f"search-{num:03d}.md")
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
            lines.append(f"Relevant extract: {r.extract}")
        lines.append("")

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    return path


# ── Orchestration ────────────────────────────────────────────────────

def execute_search(workspace, task_number, query, cache, provider, max_results=5):
    """Execute a search, cache it, store results, return (path, num_results, error)."""
    normalized = _canonical_query(query)
    cached = cache.get(query)
    if cached is not None:
        path = _write_search_result(workspace, task_number, query, cached, provider_name="cache")
        return path, len(cached), None

    try:
        results = provider.search(query, max_results=max_results)
    except Exception as exc:
        return None, 0, str(exc)

    if not results:
        return None, 0, "no results returned"

    cache.put(query, results)
    path = _write_search_result(workspace, task_number, query, results, provider_name="live")
    return path, len(results), None
