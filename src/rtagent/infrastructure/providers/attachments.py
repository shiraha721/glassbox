"""References to attached images in provider requests, filled in only as a request is sent.

Prepared requests, previews and recorded wire bodies keep `attachment-data-url:<hash>` or
`attachment-base64:<hash>` strings. The model call sets `attachment_data` for its own
messages' images; the HTTP request hook then replaces exactly those strings."""

import json
from contextvars import ContextVar
from typing import Any

import httpx

DATA_URL = "attachment-data-url:"
BASE64 = "attachment-base64:"

# hash -> (media type, base64 data), for the images of the request being sent.
attachment_data: ContextVar[dict[str, tuple[str, str]] | None] = ContextVar(
    "rtagent_attachment_data", default=None
)


def substitute(value: Any, data: dict[str, tuple[str, str]]) -> Any:
    """Replace whole-string references to the given images; everything else is unchanged."""
    if isinstance(value, dict):
        return {key: substitute(item, data) for key, item in value.items()}
    if isinstance(value, list):
        return [substitute(item, data) for item in value]
    if isinstance(value, str):
        if value.startswith(DATA_URL) and value[len(DATA_URL) :] in data:
            media_type, encoded = data[value[len(DATA_URL) :]]
            return f"data:{media_type};base64,{encoded}"
        if value.startswith(BASE64) and value[len(BASE64) :] in data:
            return data[value[len(BASE64) :]][1]
    return value


def fill_attachments(request: httpx.Request) -> None:
    """Swap the checked and recorded body for one with image data, just before sending."""
    data = attachment_data.get()
    if not data:
        return
    content = json.dumps(substitute(json.loads(request.content), data)).encode()
    request.stream = httpx.ByteStream(content)
    request._content = content
    request.headers["Content-Length"] = str(len(content))
