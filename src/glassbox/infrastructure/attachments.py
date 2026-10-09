"""Images attached to tasks, stored once by content hash in the data directory."""

import base64
import hashlib
import os
import re
from pathlib import Path
from tempfile import NamedTemporaryFile

from glassbox.domain import AppError, Attachment

IMAGE_LIMIT = 5 * 1024 * 1024
HASH = re.compile(r"[0-9a-f]{64}")


def sniff(head: bytes) -> str | None:
    """The image type from its first bytes; SVG and anything else is refused."""
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    return None


class AttachmentStore:
    """Save and read attached images; the name of each file is the SHA-256 of its bytes."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def _path(self, identifier: str) -> Path:
        if not HASH.fullmatch(identifier):
            raise AppError("attachment_missing", "Unknown attachment.", 404)
        return self.root / identifier

    def save(self, data: bytes) -> Attachment:
        """Store a PNG, JPEG, GIF or WebP image of at most 5 MiB."""
        if len(data) > IMAGE_LIMIT:
            raise AppError("attachment_size", "Images can be at most 5 MiB.", 413)
        media_type = sniff(data[:16])
        if media_type is None:
            raise AppError("attachment_type", "Attach a PNG, JPEG, GIF or WebP image.", 415)
        identifier = hashlib.sha256(data).hexdigest()
        target = self.root / identifier
        if not target.exists():
            self.root.mkdir(parents=True, exist_ok=True)
            with NamedTemporaryFile(dir=self.root, delete=False, prefix=".upload-") as handle:
                temporary = Path(handle.name)
                handle.write(data)
            try:
                os.replace(temporary, target)
            finally:
                temporary.unlink(missing_ok=True)
        return Attachment(hash=identifier, media_type=media_type, size=len(data))

    def info(self, identifier: str) -> Attachment:
        """Describe a stored image without reading all of it."""
        path = self._path(identifier)
        try:
            with path.open("rb") as handle:
                head = handle.read(16)
            size = path.stat().st_size
        except OSError:
            raise AppError(
                "attachment_missing", "An attached image is no longer stored.", 404
            ) from None
        media_type = sniff(head)
        if media_type is None:
            raise AppError("attachment_missing", "An attached image is unreadable.", 404)
        return Attachment(hash=identifier, media_type=media_type, size=size)

    def load(self, identifier: str) -> bytes:
        """The bytes of a stored image."""
        try:
            return self._path(identifier).read_bytes()
        except OSError:
            raise AppError(
                "attachment_missing", "An attached image is no longer stored.", 404
            ) from None

    def encoded(self, attachments: list[Attachment]) -> dict[str, tuple[str, str]]:
        """Base64 data for each attachment, keyed by hash, for filling a provider request."""
        return {
            item.hash: (item.media_type, base64.b64encode(self.load(item.hash)).decode())
            for item in attachments
        }
