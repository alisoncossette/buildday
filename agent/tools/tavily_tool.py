"""Stead — Tavily web-grounding tools (search + extract) for the Claude tool runner.

Two thin, swappable @beta_tool adapters that let Stead ground its answers in the live web:
  - web_search(query): ask Tavily for the top web results (+ an optional one-line answer),
  - web_extract(url):  pull the clean, readable content of a single page.

Pure-Python over the Tavily REST API (https://docs.tavily.com) using httpx — no heavy SDK. Reads
TAVILY_API_KEY from the environment. With NO key set it returns a clear, friendly OFFLINE message
instead of raising, so the whole agent stays demoable on a hotspot with no network or secrets.

Prereqs:  pip install httpx   (already a Stead dep) ; export TAVILY_API_KEY=tvly-...
Docs:     https://docs.tavily.com/documentation/api-reference/endpoint/search
          https://docs.tavily.com/documentation/api-reference/endpoint/extract
"""
import os

import httpx

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

try:
    # Importable both as `agent.tools.tavily_tool` and as a bare `tools.tavily_tool` (see stead_agent).
    from anthropic import beta_tool
except Exception:  # pragma: no cover - lets the module import for offline unit tests without the SDK
    def beta_tool(fn):
        return fn

TAVILY_API_KEY = os.environ.get("TAVILY_API_KEY")
BASE = "https://api.tavily.com"

# Confirmed 2026-06 against https://docs.tavily.com : POST {BASE}/search and {BASE}/extract,
# auth via `Authorization: Bearer tvly-...`, key fields below.
_OFFLINE = (
    "OFFLINE: web grounding is unavailable — TAVILY_API_KEY is not set. "
    "I can't search the live web right now; tell the owner to set TAVILY_API_KEY "
    "(get one at https://app.tavily.com) to enable it. Don't fabricate web results."
)


def _auth() -> dict:
    return {"Authorization": f"Bearer {TAVILY_API_KEY}"}


@beta_tool
def web_search(query: str, max_results: int = 5) -> str:
    """Search the live web with Tavily and return the top results to ground an answer.

    Use this when you need current, real-world facts you don't already know (today's news, a price,
    an address, hours, a definition). Returns a short Tavily-written answer plus the top sources with
    their titles and URLs. Returns a clear OFFLINE message (never a fake answer) if no API key is set.

    Args:
        query: What to search for, in plain language.
        max_results: How many web results to return (1-20, default 5).
    """
    if not TAVILY_API_KEY:
        return _OFFLINE

    payload = {
        "query": query,
        "max_results": max(1, min(int(max_results), 20)),
        "search_depth": "basic",
        "include_answer": True,
    }
    try:
        r = httpx.post(f"{BASE}/search", headers=_auth(), json=payload, timeout=30.0)
        r.raise_for_status()
        data = r.json()
    except httpx.HTTPStatusError as e:
        return f"web_search failed: Tavily returned HTTP {e.response.status_code}. Don't fabricate results."
    except Exception as e:  # network down, timeout, bad JSON — degrade gracefully, never invent facts.
        return f"web_search failed ({type(e).__name__}): {e}. The web is unreachable; don't fabricate results."

    lines = []
    answer = (data.get("answer") or "").strip()
    if answer:
        lines.append(f"Answer: {answer}")
    results = data.get("results") or []
    if not results:
        lines.append("No web results found.")
    for i, res in enumerate(results, 1):
        title = (res.get("title") or "").strip() or "(untitled)"
        url = res.get("url") or ""
        snippet = " ".join((res.get("content") or "").split())[:300]
        lines.append(f"{i}. {title}\n   {url}\n   {snippet}")
    return "\n".join(lines)


@beta_tool
def web_extract(url: str) -> str:
    """Fetch and return the clean, readable text content of a single web page via Tavily Extract.

    Use this after web_search when you need the full text of a specific page (an article, a menu,
    a docs page) rather than just a snippet. Returns the page content as markdown, or a clear OFFLINE
    message if no API key is set, or a short error if the page can't be extracted.

    Args:
        url: The full page URL to extract, e.g. https://example.com/article.
    """
    if not TAVILY_API_KEY:
        return _OFFLINE

    payload = {"urls": url, "extract_depth": "basic", "format": "markdown"}
    try:
        r = httpx.post(f"{BASE}/extract", headers=_auth(), json=payload, timeout=60.0)
        r.raise_for_status()
        data = r.json()
    except httpx.HTTPStatusError as e:
        return f"web_extract failed: Tavily returned HTTP {e.response.status_code}. Don't fabricate the page."
    except Exception as e:
        return f"web_extract failed ({type(e).__name__}): {e}. The page is unreachable; don't fabricate it."

    results = data.get("results") or []
    if not results:
        failed = data.get("failed_results") or []
        why = (failed[0].get("error") if failed else "no content returned")
        return f"web_extract: could not extract {url} ({why}). Don't fabricate the page contents."

    content = (results[0].get("raw_content") or "").strip()
    if not content:
        return f"web_extract: {url} returned no readable content."
    # Keep the tool result compact so it doesn't blow the context window on long pages.
    if len(content) > 8000:
        content = content[:8000] + "\n…[truncated]"
    return f"Extracted content from {url}:\n\n{content}"


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1].startswith("http"):
        print(web_extract(sys.argv[1]))
    else:
        print(web_search(" ".join(sys.argv[1:]) or "What is Tavily?"))
