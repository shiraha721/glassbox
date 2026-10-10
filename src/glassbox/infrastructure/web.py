"""Web search and page reading for the model, run by the service, never from the sandbox.

Both send model-written text (a query, a URL) off this computer, so they are separate tools
that default to asking. Search keys are resolved on the server and never returned."""

import json
import re
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx

from glassbox.domain import AppError, SearchSettings
from glassbox.infrastructure.credentials import CredentialStore
from glassbox.infrastructure.workspace.downloads import public_address

SEARCH_ENDPOINTS = {
    "brave": "https://api.search.brave.com/res/v1/web/search",
    "tavily": "https://api.tavily.com/search",
}
RESPONSE_LIMIT = 2 * 1024 * 1024
TEXT_LIMIT = 48_000
MAX_REDIRECTS = 3
USER_AGENT = "glassbox-read-url/1"
# Tests replace this to answer requests without a network.
transport: httpx.AsyncBaseTransport | None = None


def client() -> httpx.AsyncClient:
    """An HTTP client that ignores proxy variables and never follows redirects itself."""
    return httpx.AsyncClient(
        trust_env=False, follow_redirects=False, timeout=20, transport=transport
    )


async def bounded_body(response: httpx.Response, limit: int) -> tuple[bytes, bool]:
    """Read at most `limit` decoded bytes; True when the body was longer."""
    body = bytearray()
    async for chunk in response.aiter_bytes():
        body.extend(chunk[: limit + 1 - len(body)])
        if len(body) > limit:
            return bytes(body[:limit]), True
    return bytes(body), False


class _TextExtractor(HTMLParser):
    """Collect readable text and the title, skipping scripts, styles and other non-text."""

    SKIP = {"script", "style", "noscript", "template", "svg", "head", "iframe", "object"}
    BLOCKS = {
        "p", "div", "br", "li", "ul", "ol", "tr", "table", "section", "article", "header",
        "footer", "nav", "aside", "main", "h1", "h2", "h3", "h4", "h5", "h6", "pre",
        "blockquote", "hr", "dd", "dt", "figure", "figcaption", "form",
    }  # fmt: skip

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.title: list[str] = []
        self.skipping = 0
        self.in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "title":
            self.in_title = True
        elif tag in self.SKIP:
            self.skipping += 1
        elif tag in self.BLOCKS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self.in_title = False
        elif tag in self.SKIP:
            self.skipping = max(0, self.skipping - 1)
        elif tag in self.BLOCKS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self.in_title:
            self.title.append(data)
        elif not self.skipping:
            self.parts.append(data)


def html_text(html: str) -> tuple[str, str]:
    """Plain text and title of an HTML document, with whitespace collapsed per line."""
    parser = _TextExtractor()
    parser.feed(html)
    parser.close()
    lines = (
        re.sub(r"[ \t\r\f\v]+", " ", line).strip() for line in "".join(parser.parts).split("\n")
    )
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
    return text, " ".join("".join(parser.title).split())


def _plain(value: Any, limit: int) -> str:
    """A bounded single-line string from a search API field, without markup."""
    text = re.sub(r"<[^>]+>", "", str(value or ""))
    return " ".join(text.split())[:limit]


async def web_search(
    settings: SearchSettings, credentials: CredentialStore, query: str, count: int = 5
) -> dict[str, Any]:
    """Ask the configured search service; return titles, URLs and snippets."""
    if settings.provider == "none":
        raise AppError(
            "search_unconfigured", "No search service is configured. Set one in Settings → Tools."
        )
    key = credentials.resolve(settings.credential_ref) if settings.provider != "searxng" else ""
    if settings.provider != "searxng" and not key:
        raise AppError(
            "search_key", f"The search key {settings.credential_ref} is not set in Settings."
        )
    request: tuple[str, str, dict[str, Any]]
    if settings.provider == "brave":
        request = (
            "GET",
            SEARCH_ENDPOINTS["brave"],
            {
                "params": {"q": query, "count": count},
                "headers": {"X-Subscription-Token": key, "Accept": "application/json"},
            },
        )
    elif settings.provider == "tavily":
        request = (
            "POST",
            SEARCH_ENDPOINTS["tavily"],
            {
                "json": {"query": query, "max_results": count},
                "headers": {"Authorization": f"Bearer {key}"},
            },
        )
    else:
        request = (
            "GET",
            settings.base_url.rstrip("/") + "/search",
            {"params": {"q": query, "format": "json"}},
        )
    method, url, options = request
    try:
        async with client() as http, http.stream(method, url, **options) as response:
            body, truncated = await bounded_body(response, RESPONSE_LIMIT)
            status = response.status_code
    except httpx.HTTPError as exc:
        raise AppError(
            "search_failed", f"The search service could not be reached ({type(exc).__name__})."
        ) from None
    if status != 200:
        raise AppError("search_failed", f"The search service returned HTTP {status}.")
    try:
        data = json.loads(body) if not truncated else {}
    except ValueError:
        data = {}
    if not isinstance(data, dict):
        data = {}
    if settings.provider == "brave":
        web = data.get("web")
        items = web.get("results", []) if isinstance(web, dict) else []
        snippet = "description"
    else:
        items = data.get("results", [])
        snippet = "content"
    results = [
        {
            "title": _plain(item.get("title"), 300),
            "url": str(item.get("url") or "")[:2048],
            "snippet": _plain(item.get(snippet), 500),
        }
        for item in (items if isinstance(items, list) else [])
        if isinstance(item, dict) and item.get("url")
    ][:count]
    return {"query": query, "provider": settings.provider, "results": results}


def _checked(url: str) -> tuple[str, str]:
    """Validate a URL to read; returns its host and the path with query."""
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.port not in {None, 443}
        or parsed.username
        or parsed.password
    ):
        raise AppError("network_url", "Use a public HTTPS URL without credentials or a port.")
    return parsed.hostname, urlunsplit(("", "", parsed.path or "/", parsed.query, ""))


async def read_url(url: str) -> dict[str, Any]:
    """Read a public HTTPS page as text, re-checking every redirect hop's address."""
    current = url
    for _hop in range(MAX_REDIRECTS + 1):
        host, target = _checked(current)
        address = await public_address(host)
        authority = f"[{address}]" if ":" in address else address
        try:
            async with (
                client() as http,
                http.stream(
                    "GET",
                    f"https://{authority}{target}",
                    headers={
                        "Host": host,
                        "Accept": "text/html, text/plain;q=0.9, application/json;q=0.8, */*;q=0.1",
                        "Accept-Encoding": "gzip, deflate",
                        "User-Agent": USER_AGENT,
                    },
                    extensions={"sni_hostname": host},
                ) as response,
            ):
                if response.status_code in {301, 302, 303, 307, 308}:
                    location = response.headers.get("location")
                    if not location:
                        raise AppError("read_failed", "A redirect named no new location.")
                    current = urljoin(current, location)
                    continue
                if response.status_code != 200:
                    raise AppError("read_failed", f"The page returned HTTP {response.status_code}.")
                kind = response.headers.get("content-type", "").split(";")[0].strip().lower()
                if not (
                    kind.startswith("text/")
                    or kind in {"application/json", ""}
                    or kind.endswith(("+xml", "+json"))
                    or kind == "application/xhtml+xml"
                ):
                    raise AppError(
                        "read_unsupported", f"read_url reads text pages; this is {kind}."
                    )
                body, cut = await bounded_body(response, RESPONSE_LIMIT)
                encoding = response.charset_encoding or "utf-8"
        except httpx.HTTPError as exc:
            raise AppError(
                "read_failed", f"The page could not be read ({type(exc).__name__})."
            ) from None
        try:
            decoded = body.decode(encoding, "replace")
        except LookupError:
            decoded = body.decode("utf-8", "replace")
        title = ""
        if "html" in kind or (not kind and "<html" in decoded[:2000].lower()):
            decoded, title = html_text(decoded)
        text = decoded[:TEXT_LIMIT]
        return {
            "url": current,
            "title": title,
            "content_type": kind,
            "text": text,
            "truncated": cut or len(decoded) > TEXT_LIMIT,
        }
    raise AppError("read_failed", f"More than {MAX_REDIRECTS} redirects.")
