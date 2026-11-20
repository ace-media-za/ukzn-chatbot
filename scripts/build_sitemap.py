"""Crawl one host breadth-first and write its HTML page URLs, one per line.

Only the start URL's host is crawled (``www.`` is treated as the same host);
subdomains, assets, documents, WordPress internals and query-string URLs are
skipped. Pagination, ``/tag/`` and ``/category/`` archive pages are followed
to discover links but are not written. The output file is
``urls_<host>_simplescraper.txt`` in the current directory, and progress is
logged to stderr.

Usage: .venv/bin/python scripts/build_sitemap.py https://ukzn.ac.za/
"""

import re
import sys
import time
from collections import deque
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urldefrag, urljoin, urlsplit

import httpx

SKIP_PATH = re.compile(r"^/(wp-content|wp-includes|wp-json|wp-admin|feed|comments/feed|xmlrpc)|/feed/?$")
SKIP_EXT = re.compile(r"\.(pdf|docx?|xlsx?|pptx?|zip|jpe?g|png|gif|svg|webp|mp4|mp3|css|js|xml|ico)$", re.I)
# Archive listings: crawled to discover links but not listed, since they only repeat article excerpts.
PAGINATION = re.compile(r"/page/\d+/?$|^/(tag|category)/")


class LinkParser(HTMLParser):
    """Collect the ``href`` values of all ``<a>`` tags in a document, in order."""

    def __init__(self) -> None:
        """Create a parser with an empty ``hrefs`` list."""

        super().__init__()
        self.hrefs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Record the stripped ``href`` of an anchor tag; ignore other tags."""

        if tag == "a":
            self.hrefs.extend(v.strip() for k, v in attrs if k == "href" and v)


def bare_host(host: str) -> str:
    """Return ``host`` lower-cased without a leading ``www.``."""

    return host.lower().removeprefix("www.")


def page_key(url: str) -> str | None:
    """Return a dedup key for a crawlable page URL, or None to skip it.

    The key is the bare host plus the lower-cased path without a trailing
    slash, so ``https://www.x/About/`` and ``https://x/about`` collide.

    Args:
        url: Absolute URL without a fragment.
    """

    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or parts.query:
        return None
    if SKIP_PATH.search(parts.path) or SKIP_EXT.search(parts.path):
        return None
    return f"{bare_host(parts.netloc)}{parts.path.rstrip('/').lower()}"


def fetch(client: httpx.Client, url: str, attempts: int = 5) -> httpx.Response | None:
    """GET ``url``, retrying transport errors with exponential backoff.

    Waits 1, 2, 4, ... seconds between attempts so transient network drops
    don't lose pages. HTTP error statuses are returned, not retried.

    Args:
        client: HTTP client to send the request with.
        url: URL to fetch.
        attempts: Maximum number of tries.

    Returns:
        The response, or None if every attempt failed.
    """

    for attempt in range(attempts):
        try:
            return client.get(url)
        except httpx.HTTPError as exc:
            print(f"retry {attempt + 1}/{attempts} {url}: {exc}", file=sys.stderr)
            time.sleep(2**attempt)
    print(f"error {url}: gave up", file=sys.stderr)
    return None


def crawl(start: str) -> list[str]:
    """Crawl breadth-first from ``start`` and return the HTML page URLs found.

    Args:
        start: Absolute URL whose host bounds the crawl.

    Returns:
        Final (post-redirect) URLs of 200 ``text/html`` pages, in discovery
        order, without duplicates.
    """

    host = bare_host(urlsplit(start).netloc)
    queue = deque([start])
    seen = {page_key(start)}
    found: list[str] = []

    with httpx.Client(follow_redirects=True, timeout=20, headers={"User-Agent": "sitemap-builder/1.0"}) as client:
        while queue:
            url = queue.popleft()
            response = fetch(client, url)
            if response is None:
                continue
            final = str(response.url)
            if (
                response.status_code != 200
                or "text/html" not in response.headers.get("content-type", "")
                or bare_host(urlsplit(final).netloc) != host
            ):
                continue
            if not PAGINATION.search(urlsplit(final).path):
                found.append(final)
            print(f"[{len(found)} found, {len(queue)} queued] {final}", file=sys.stderr)

            parser = LinkParser()
            parser.feed(response.text)
            for href in parser.hrefs:
                link = urldefrag(urljoin(final, href)).url
                key = page_key(link)
                if key and key.split("/", 1)[0] == host and key not in seen:
                    seen.add(key)
                    queue.append(link)
    return list(dict.fromkeys(found))


def main() -> None:
    """Crawl the URL given as the first argument and write the URL list file."""

    start = sys.argv[1]
    urls = crawl(start)
    out = Path(f"urls_{bare_host(urlsplit(start).netloc).replace('.', '_')}_simplescraper.txt")
    out.write_text("\n".join(urls) + "\n")
    print(f"wrote {len(urls)} urls to {out}", file=sys.stderr)


if __name__ == "__main__":
    main()
