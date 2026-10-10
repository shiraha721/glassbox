"""Bounded HTTPS downloads pinned to verified public addresses, separate from command networking."""

import asyncio
import hashlib
import ipaddress
import os
import socket
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx

from glassbox.domain import AppError
from glassbox.infrastructure.workspace.files import Workspace

DOWNLOAD_LIMIT = 16 * 1024 * 1024
MAX_REDIRECTS = 5
REDIRECTS = {301, 302, 303, 307, 308}


async def public_address(host: str) -> str:
    """Resolve once and reject every non-public answer before pinning the actual connection."""
    answers = await asyncio.get_running_loop().getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    addresses = list(dict.fromkeys(str(item[4][0]) for item in answers))
    if not addresses or any(not ipaddress.ip_address(item).is_global for item in addresses):
        raise AppError("network_denied", "Only public Internet addresses are allowed.", 403)
    return addresses[0]


def save_download(workspace: Workspace, relative: str, body: bytes) -> None:
    """Create only a new allowed file and remove an incomplete write on local failure."""
    with workspace.lock:
        target = workspace.path(relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        created = False
        try:
            with target.open("xb") as output:
                created = True
                output.write(body)
                output.flush()
                os.fsync(output.fileno())
        except FileExistsError as exc:
            raise AppError(
                "file_conflict", "Downloads require a new destination file.", 409
            ) from exc
        except BaseException:
            if created:
                target.unlink(missing_ok=True)
            raise
        workspace.record_change(target, None, body)


def allowed_url(url: str, hosts: list[str], redirect: bool = False):
    """Check one URL against the download policy: HTTPS on port 443 to an allowed exact host.

    The model's own URL may not carry a query; a redirect target chosen by an allowed host
    may, since signed download links (GitHub releases, Hugging Face files) need one."""
    parsed = urlsplit(url)
    host = parsed.hostname
    if (
        parsed.scheme != "https"
        or not host
        or parsed.port not in {None, 443}
        or parsed.username
        or parsed.password
        or parsed.fragment
        or (parsed.query and not redirect)
    ):
        raise AppError(
            "network_url",
            (
                f"The download redirected to an unsupported URL ({parsed.scheme}://{host})."
                if redirect
                else "Use a public HTTPS URL without credentials, query or fragment."
            ),
        )
    if host not in hosts:
        raise AppError(
            "network_denied",
            (
                f"The download redirected to {host}, which is not an allowed download host. "
                "Allow it in Settings → Sandbox if you trust it."
                if redirect
                else f"Allow the exact download host {host} in Settings first."
            ),
            403,
        )
    return parsed


async def fetch_url(
    workspace: Workspace, hosts: list[str], url: str, path: str, sha256: str | None = None
) -> dict:
    """Fetch one bounded identity-encoded HTTPS response without proxies or secrets.

    Redirects are followed only to allowed hosts, at most five, and every hop is checked and
    pinned to a resolved public address like the first request."""
    parsed = allowed_url(url, hosts)
    target = workspace.path(path)
    if target.exists():
        raise AppError("file_conflict", "Downloads require a new destination file.", 409)
    body = bytearray()
    async with httpx.AsyncClient(trust_env=False, follow_redirects=False, timeout=30) as client:
        for hop in range(MAX_REDIRECTS + 1):
            host = parsed.hostname
            assert host is not None
            address = await public_address(host)
            authority = f"[{address}]" if ":" in address else address
            pinned = urlunsplit(("https", authority, parsed.path or "/", parsed.query, ""))
            async with client.stream(
                "GET",
                pinned,
                headers={"Host": host, "Accept-Encoding": "identity"},
                extensions={"sni_hostname": host},
            ) as response:
                if response.status_code in REDIRECTS:
                    location = response.headers.get("location")
                    if not location:
                        raise AppError("download_failed", "A redirect gave no location.")
                    if hop == MAX_REDIRECTS:
                        raise AppError(
                            "download_failed",
                            f"Download stopped: more than {MAX_REDIRECTS} redirects.",
                        )
                    parsed = allowed_url(urljoin(urlunsplit(parsed), location), hosts, True)
                    continue
                if response.status_code != 200:
                    raise AppError(
                        "download_failed", f"Download returned HTTP {response.status_code}."
                    )
                if response.headers.get("content-encoding", "identity").lower() not in {
                    "identity",
                    "",
                }:
                    raise AppError(
                        "download_encoding", "Compressed HTTP transfer encoding is unsupported."
                    )
                async for chunk in response.aiter_raw():
                    if len(body) + len(chunk) > DOWNLOAD_LIMIT:
                        raise AppError("download_limit", "Download exceeds 16 MiB.")
                    body.extend(chunk)
                break
        else:  # Not reached: the last hop downloads or raises on another redirect.
            raise AppError(
                "download_failed", f"Download stopped: more than {MAX_REDIRECTS} redirects."
            )
    actual = hashlib.sha256(body).hexdigest()
    if sha256 is not None and actual != sha256.lower():
        raise AppError("download_hash", "Downloaded content does not match the expected SHA-256.")
    await asyncio.to_thread(save_download, workspace, path, bytes(body))
    return {
        "url": url,
        "path": path,
        "bytes": len(body),
        "sha256": actual,
        "final_host": parsed.hostname,
        "address": address,
        "redirects": hop,
        "network_policy": "explicit-public-https",
    }
