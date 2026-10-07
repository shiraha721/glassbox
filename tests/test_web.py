"""Web search and page reading: server-side keys, public addresses only, bounded text."""

import json

import httpx
import pytest
from pydantic import ValidationError

from rtagent.domain import AppError, SearchSettings
from rtagent.infrastructure import web
from rtagent.infrastructure.credentials import CredentialStore


def test_html_becomes_readable_text_without_scripts():
    text, title = web.html_text(
        "<html><head><title> The  Page </title><style>p{}</style></head><body>"
        "<script>alert(1)</script><h1>Heading</h1><p>First &amp; second</p>"
        "<ul><li>one</li><li>two</li></ul></body></html>"
    )
    assert title == "The Page"
    assert text == "Heading\n\nFirst & second\n\none\n\ntwo"


def test_search_settings_must_be_usable():
    with pytest.raises(ValidationError):
        SearchSettings(provider="brave")
    with pytest.raises(ValidationError):
        SearchSettings(provider="searxng", base_url="localhost:8080")
    assert SearchSettings(provider="searxng", base_url="http://127.0.0.1:8080").provider


@pytest.fixture
def answer(monkeypatch):
    """Route the module's HTTP client to a handler and record each request."""
    seen: list[httpx.Request] = []

    def install(handler):
        def recorded(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return handler(request)

        monkeypatch.setattr(web, "transport", httpx.MockTransport(recorded))
        return seen

    return install


async def test_each_search_service_gets_its_request_and_the_key_stays_server_side(answer, tmp_path):
    credentials = CredentialStore(tmp_path / "credentials.json")
    credentials.save("SEARCH_KEY", "secret-search-key")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.search.brave.com":
            body = {"web": {"results": [{"title": "<b>Brave</b>", "url": "https://a.example", "description": "about <em>a</em>"}]}}  # fmt: skip
        elif request.url.host == "api.tavily.com":
            body = {"results": [{"title": "Tavily", "url": "https://b.example", "content": "b"}]}
        else:
            body = {"results": [{"title": "Searx", "url": "https://c.example", "content": "c"}, {"title": "no url"}]}  # fmt: skip
        return httpx.Response(200, json=body)

    seen = answer(handler)
    brave = SearchSettings(provider="brave", credential_ref="SEARCH_KEY")
    result = await web.web_search(brave, credentials, "agent studio", 3)
    assert result["results"] == [
        {"title": "Brave", "url": "https://a.example", "snippet": "about a"}
    ]
    assert seen[-1].headers["X-Subscription-Token"] == "secret-search-key"
    assert seen[-1].url.params["q"] == "agent studio" and seen[-1].url.params["count"] == "3"
    assert "secret-search-key" not in json.dumps(result)

    tavily = SearchSettings(provider="tavily", credential_ref="SEARCH_KEY")
    result = await web.web_search(tavily, credentials, "q", 2)
    assert seen[-1].headers["Authorization"] == "Bearer secret-search-key"
    assert json.loads(seen[-1].content) == {"query": "q", "max_results": 2}
    assert result["results"][0]["url"] == "https://b.example"

    searx = SearchSettings(provider="searxng", base_url="http://127.0.0.1:8080/")
    result = await web.web_search(searx, credentials, "q")
    assert str(seen[-1].url) == "http://127.0.0.1:8080/search?q=q&format=json"
    assert [item["url"] for item in result["results"]] == ["https://c.example"]


async def test_search_failures_are_explained_without_the_key(answer, tmp_path):
    credentials = CredentialStore(tmp_path / "credentials.json")
    settings = SearchSettings(provider="brave", credential_ref="MISSING_KEY")
    with pytest.raises(AppError) as missing:
        await web.web_search(settings, credentials, "q")
    assert missing.value.code == "search_key"
    credentials.save("MISSING_KEY", "k-123")
    answer(lambda _request: httpx.Response(401, json={"error": "bad key k-123"}))
    with pytest.raises(AppError) as rejected:
        await web.web_search(settings, credentials, "q")
    assert rejected.value.message == "The search service returned HTTP 401."
    with pytest.raises(AppError) as unconfigured:
        await web.web_search(SearchSettings(), credentials, "q")
    assert unconfigured.value.code == "search_unconfigured"


async def test_pages_are_read_through_pinned_public_addresses(answer, monkeypatch):
    """Each hop is resolved and checked; the connection goes to that address with SNI."""
    resolved = []

    async def public(host: str) -> str:
        resolved.append(host)
        return {"first.example": "93.184.216.34", "second.example": "93.184.216.35"}[host]

    monkeypatch.setattr(web, "public_address", public)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.headers["Host"] == "first.example":
            return httpx.Response(302, headers={"Location": "https://second.example/doc?x=1"})
        return httpx.Response(
            200,
            headers={"Content-Type": "text/html; charset=utf-8"},
            content=b"<title>Doc</title><p>Body text</p>",
        )

    seen = answer(handler)
    page = await web.read_url("https://first.example/start")
    assert resolved == ["first.example", "second.example"]
    assert [str(request.url) for request in seen] == [
        "https://93.184.216.34/start",
        "https://93.184.216.35/doc?x=1",
    ]
    assert seen[1].extensions["sni_hostname"] == "second.example"
    assert page == {
        "url": "https://second.example/doc?x=1",
        "title": "Doc",
        "content_type": "text/html",
        "text": "Body text",
        "truncated": False,
    }


@pytest.mark.parametrize(
    "location,code",
    [("https://127.0.0.1/admin", "network_denied"), ("http://first.example/", "network_url")],
)
async def test_redirects_cannot_reach_private_addresses_or_plain_http(
    answer, monkeypatch, location, code
):
    original = web.public_address

    async def public(host: str) -> str:
        return "93.184.216.34" if host == "first.example" else await original(host)

    monkeypatch.setattr(web, "public_address", public)
    answer(lambda _request: httpx.Response(302, headers={"Location": location}))
    with pytest.raises(AppError) as denied:
        await web.read_url("https://first.example/")
    assert denied.value.code == code


async def test_binary_pages_and_endless_redirects_are_refused(answer, monkeypatch):
    async def public(_host: str) -> str:
        return "93.184.216.34"

    monkeypatch.setattr(web, "public_address", public)
    answer(lambda _r: httpx.Response(200, headers={"Content-Type": "image/png"}, content=b"x"))
    with pytest.raises(AppError) as binary:
        await web.read_url("https://first.example/image")
    assert binary.value.code == "read_unsupported"
    answer(lambda _r: httpx.Response(302, headers={"Location": "/again"}))
    with pytest.raises(AppError) as looping:
        await web.read_url("https://first.example/")
    assert looping.value.message == "More than 3 redirects."


async def test_search_is_offered_only_once_a_service_is_configured(studio_client):
    """The tool stays out of prompts until Settings name a search service."""
    _client, app = studio_client
    studio = app.state.studio
    config = await studio.repository.config()
    names = {tool.name for tool in studio.registry.selected(config)}
    assert "read_url" in names and "web_search" not in names
    config.search = SearchSettings(provider="searxng", base_url="https://search.example")
    assert "web_search" in {tool.name for tool in studio.registry.selected(config)}
