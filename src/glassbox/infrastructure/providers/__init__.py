"""Concrete infrastructure providers responsibilities; importing the package starts no work."""

import hashlib
import re

# Identifies Glassbox to model services. Gateways such as OpenCode Go ask clients to name
# themselves rather than send a generic SDK or HTTP-library agent.
USER_AGENT = "glassbox/0.1.0"

PORTABLE_ID = re.compile(r"[A-Za-z0-9_-]{1,40}")


def portable_id(identifier: str, source: str) -> str:
    """A tool-call ID that every request format accepts, for a message of the given source.

    Earlier turns may come from another provider, whose IDs another format can reject: the
    Messages API accepts only letters, digits, '_' and '-', and Chat Completions at most 40
    characters. Such an ID from history is replaced by a stable hash, the same for the call and
    its result, so their pairing is kept. The current Run's IDs come from its own provider and
    go back exactly as received."""
    if source != "history" or PORTABLE_ID.fullmatch(identifier):
        return identifier
    return "call_" + hashlib.sha256(identifier.encode()).hexdigest()[:24]
