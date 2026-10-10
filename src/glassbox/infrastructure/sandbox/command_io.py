"""Bridge native command pipes to bounded asynchronous progress and explicit stdin."""

import asyncio
import json
import queue
import threading
from dataclasses import dataclass, field

from glassbox.domain.execution import OutputCallback

# Per-stream character budgets; both fit a 64 KiB tool result with its JSON envelope.
OUTPUT_HEAD = 8_000
OUTPUT_TAIL = 16_000


@dataclass
class CommandControl:
    """Hold only the owned command's stop signal and bounded input queue, never host handles."""

    stop: threading.Event = field(default_factory=threading.Event)
    input_stop: threading.Event = field(default_factory=threading.Event)
    inputs: queue.Queue[bytes | None] = field(default_factory=lambda: queue.Queue(maxsize=16))
    ready: asyncio.Event = field(default_factory=asyncio.Event)
    pid: int | None = None
    input_closed: bool = False
    loop: asyncio.AbstractEventLoop = field(default_factory=asyncio.get_running_loop)

    def started(self, pid: int) -> None:
        """Publish the actual sandbox process identity from either native execution thread."""
        self.pid = pid
        self.loop.call_soon_threadsafe(self.ready.set)

    def feed(self, text: str = "", eof: bool = False) -> None:
        """Queue explicit UTF-8 pipe input, rejecting closed or saturated channels."""
        if self.input_closed or self.stop.is_set() or self.input_stop.is_set():
            raise ValueError("Command input is closed.")
        encoded = text.encode()
        if len(encoded) > 8192:
            raise ValueError("Command input exceeds 8 KiB.")
        # Producers share one event loop; the native consumer can only free capacity.
        if self.inputs.maxsize - self.inputs.qsize() < bool(encoded) + bool(eof):
            raise ValueError("Command input is full; wait for the process to consume it.")
        try:
            if encoded:
                self.inputs.put_nowait(encoded)
            if eof:
                self.inputs.put_nowait(None)
                self.input_closed = True
        except queue.Full as exc:
            raise ValueError("Command input is full; wait for the process to consume it.") from exc


class OutputBuffer:
    """Keep the start and the latest end of one decoded stream within a fixed budget.

    Command summaries, failures and server logs usually appear last, so a prefix alone hides
    what matters most. The marker states exactly how much text was dropped in between."""

    def __init__(self, head: int = OUTPUT_HEAD, tail: int = OUTPUT_TAIL) -> None:
        """Start empty with character budgets for the retained beginning and end."""
        self.head_limit, self.tail_limit = head, tail
        self.head, self.tail, self.omitted = "", "", 0

    def append(self, text: str) -> None:
        """Fill the head first, then keep only the newest tail characters."""
        if len(self.head) < self.head_limit:
            room = self.head_limit - len(self.head)
            self.head, text = self.head + text[:room], text[room:]
        if text:
            self.tail += text
            if len(self.tail) > self.tail_limit:
                self.omitted += len(self.tail) - self.tail_limit
                self.tail = self.tail[-self.tail_limit :]

    @property
    def truncated(self) -> bool:
        """Report whether any text between the head and tail was dropped."""
        return self.omitted > 0

    def text(self) -> str:
        """Render the retained text with an explicit omission marker."""
        if not self.omitted:
            return self.head + self.tail
        return f"{self.head}\n[... {self.omitted} characters omitted ...]\n{self.tail}"


class CommandOutput:
    """Own a command's bounded stdout/stderr and publish rate-limited snapshots.

    Producers may write from a native drain thread. Consumers receive the complete bounded
    text of a changed stream, so they replace stored output rather than appending deltas."""

    def __init__(self, callback: OutputCallback | None, interval: float = 0.1) -> None:
        """Bind the running loop and an optional async consumer without starting a process."""
        self.callback, self.interval = callback, interval
        self.streams = {"stdout": OutputBuffer(), "stderr": OutputBuffer()}
        self.loop = asyncio.get_running_loop()
        self.lock = threading.Lock()
        self.changed = asyncio.Event()
        self.dirty: set[str] = set()
        self.finished = False

    def write(self, stream: str, text: str) -> None:
        """Retain decoded output from the event loop or a Windows drain thread."""
        if not text:
            return
        with self.lock:
            self.streams[stream].append(text)
            self.dirty.add(stream)
        if self.callback:
            self.loop.call_soon_threadsafe(self.changed.set)

    def result(self, stream: str) -> tuple[str, bool]:
        """Return a stream's final bounded text and whether it was truncated."""
        with self.lock:
            return self.streams[stream].text(), self.streams[stream].truncated

    async def _flush(self, callback: OutputCallback) -> None:
        with self.lock:
            frames = [
                {
                    "stream": name,
                    "text": self.streams[name].text(),
                    "truncated": self.streams[name].truncated,
                }
                for name in sorted(self.dirty)
            ]
            self.dirty.clear()
        for frame in frames:
            await callback(frame)

    async def drain(self) -> None:
        """Publish coalesced snapshots until close() flushes the final state."""
        callback = self.callback
        if not callback:
            return
        while not self.finished:
            await self.changed.wait()
            self.changed.clear()
            if not self.finished:
                await asyncio.sleep(self.interval)
            await self._flush(callback)
        await self._flush(callback)

    async def close(self, reader: asyncio.Task[None]) -> None:
        """Flush retained output before a final command receipt or cancellation is recorded."""
        self.finished = True
        self.changed.set()
        await asyncio.shield(reader)


def command_result(result: dict) -> str:
    """Serialize a command result within the 64 KiB tool limit, dropping older output first.

    JSON escaping can multiply the size of retained text, so the character budgets alone
    cannot guarantee the serialized bound."""
    while len(json.dumps(result, ensure_ascii=False).encode()) > 65536:
        for key in ("stdout", "stderr"):
            result[key] = result[key][len(result[key]) // 2 :]
        result["truncated"] = True
    return json.dumps(result, ensure_ascii=False)
