"""Bounded HTTPS downloads pinned to verified public addresses, separate from command networking."""

import asyncio
import hashlib
import ipaddress
import os
import socket
from urllib.parse import urlsplit, urlunsplit

import httpx

from rtagent.domain import AppError
from rtagent.infrastructure.workspace.files import Workspace

DOWNLOAD_LIMIT = 16 * 1024 * 1024


async def public_address(host: str) -> str:
    """Resolve once and reject every non-public answer before pinning the actual connection."""
    answers = await asyncio.get_running_loop().getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    addresses = list(dict.fromkeys(item[4][0] for item in answers))
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


async def fetch_url(
    workspace: Workspace, hosts: list[str], url: str, path: str, sha256: str | None = None
) -> dict:
    """Fetch one bounded identity-encoded HTTPS response with no redirects, proxies or secrets."""
    parsed = urlsplit(url)
    host = parsed.hostname
    if (
        parsed.scheme != "https"
        or not host
        or parsed.port not in {None, 443}
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise AppError(
            "network_url", "Use a public HTTPS URL without credentials, query or fragment."
        )
    if host not in hosts:
        raise AppError(
            "network_denied", f"Allow the exact download host {host} in Settings first.", 403
        )
    target = workspace.path(path)
    if target.exists():
        raise AppError("file_conflict", "Downloads require a new destination file.", 409)
    address = await public_address(host)
    authority = f"[{address}]" if ":" in address else address
    pinned = urlunsplit(("https", authority, parsed.path or "/", "", ""))
    async with httpx.AsyncClient(trust_env=False, follow_redirects=False, timeout=30) as client:
        async with client.stream(
            "GET",
            pinned,
            headers={"Host": host, "Accept-Encoding": "identity"},
            extensions={"sni_hostname": host},
        ) as response:
            if response.status_code != 200:
                raise AppError(
                    "download_failed",
                    f"Download returned HTTP {response.status_code}; redirects are not followed.",
                )
            if response.headers.get("content-encoding", "identity").lower() not in {"identity", ""}:
                raise AppError(
                    "download_encoding", "Compressed HTTP transfer encoding is unsupported."
                )
            body = bytearray()
            async for chunk in response.aiter_raw():
                if len(body) + len(chunk) > DOWNLOAD_LIMIT:
                    raise AppError("download_limit", "Download exceeds 16 MiB.")
                body.extend(chunk)
    actual = hashlib.sha256(body).hexdigest()
    if sha256 is not None and actual != sha256.lower():
        raise AppError("download_hash", "Downloaded content does not match the expected SHA-256.")
    await asyncio.to_thread(save_download, workspace, path, bytes(body))
    return {
        "url": url,
        "path": path,
        "bytes": len(body),
        "sha256": actual,
        "address": address,
        "network_policy": "explicit-public-https",
    }
