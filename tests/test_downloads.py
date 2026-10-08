"""fetch_url follows redirects only to allowed hosts, each hop pinned to a public address."""

import httpx
import pytest

import rtagent.infrastructure.workspace.downloads as downloads
from rtagent.domain import COMMON_DOWNLOAD_HOSTS, AppError, StudioConfig
from rtagent.infrastructure.workspace.files import Workspace

HOSTS = ["github.com", "release-assets.githubusercontent.com"]


@pytest.fixture
def served(monkeypatch):
    """Route downloads to a handler keyed by the Host header instead of the network."""
    routes: dict[str, httpx.Response] = {}
    seen: list[httpx.Request] = []

    async def public(_host):
        return "140.82.112.3"

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        key = request.headers["host"] + request.url.raw_path.decode()
        return routes.get(key, httpx.Response(404))

    real = httpx.AsyncClient
    monkeypatch.setattr(downloads, "public_address", public)
    monkeypatch.setattr(
        downloads.httpx,
        "AsyncClient",
        lambda **options: real(**options, transport=httpx.MockTransport(handler)),
    )
    return routes, seen


async def test_a_release_download_follows_its_signed_redirect(tmp_path, served):
    routes, seen = served
    signed = "/asset/1?X-Amz-Signature=abc"
    routes["github.com/owner/tool/releases/download/v1/tool.tar.gz"] = httpx.Response(
        302, headers={"location": f"https://release-assets.githubusercontent.com{signed}"}
    )
    routes["release-assets.githubusercontent.com" + signed] = httpx.Response(
        200, stream=httpx.ByteStream(b"data")
    )
    result = await downloads.fetch_url(
        Workspace(tmp_path),
        HOSTS,
        "https://github.com/owner/tool/releases/download/v1/tool.tar.gz",
        "tool.tar.gz",
    )
    assert (tmp_path / "tool.tar.gz").read_bytes() == b"data"
    assert result["redirects"] == 1 and result["final_host"] == HOSTS[1]
    # Every hop connects to the checked address and names the real host for TLS.
    assert [request.url.host for request in seen] == ["140.82.112.3", "140.82.112.3"]
    assert [request.extensions["sni_hostname"] for request in seen] == HOSTS


@pytest.mark.parametrize(
    "location,code",
    [
        ("https://evil.example/payload", "network_denied"),
        ("http://release-assets.githubusercontent.com/x", "network_url"),
        ("https://release-assets.githubusercontent.com:8443/x", "network_url"),
    ],
)
async def test_redirects_leave_neither_the_allowlist_nor_https(tmp_path, served, location, code):
    routes, _seen = served
    routes["github.com/file"] = httpx.Response(302, headers={"location": location})
    with pytest.raises(AppError) as refused:
        await downloads.fetch_url(Workspace(tmp_path), HOSTS, "https://github.com/file", "f")
    assert refused.value.code == code
    assert not (tmp_path / "f").exists()


async def test_redirect_loops_and_query_urls_are_refused(tmp_path, served):
    routes, _seen = served
    routes["github.com/loop"] = httpx.Response(302, headers={"location": "/loop"})
    with pytest.raises(AppError) as loop:
        await downloads.fetch_url(Workspace(tmp_path), HOSTS, "https://github.com/loop", "f")
    assert loop.value.code == "download_failed" and "more than 5 redirects" in loop.value.message
    with pytest.raises(AppError) as query:
        await downloads.fetch_url(Workspace(tmp_path), HOSTS, "https://github.com/f?token=1", "f")
    assert query.value.code == "network_url"


def test_new_settings_allow_common_download_hosts():
    hosts = StudioConfig().network_hosts
    assert hosts == list(COMMON_DOWNLOAD_HOSTS)
    assert {"github.com", "registry.npmjs.org", "pypi.org", "huggingface.co"} <= set(hosts)
    StudioConfig(network_hosts=hosts)  # every default passes the exact-host rule
