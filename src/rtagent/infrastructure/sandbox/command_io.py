"""Bridge native command pipes to bounded asynchronous progress and explicit stdin."""

import asyncio
import queue
import threading
from dataclasses import dataclass, field
from typing import Any

from rtagent.domain.execution import OutputCallback


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


class OutputRelay:
    """Coalesce bounded native pipe fragments before calling async persistence callbacks."""

    def __init__(self, callback: OutputCallback | None) -> None:
        """Bind one call's event loop and output consumer without starting a process."""
        self.callback = callback
        self.loop = asyncio.get_running_loop()
        self.queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()

    def publish(self, stream: str, text: str, truncated: bool = False) -> None:
        """Accept already bounded decoded output from Linux or a Windows drain thread."""
        if self.callback:
            self.loop.call_soon_threadsafe(
                self.queue.put_nowait, {"stream": stream, "text": text, "truncated": truncated}
            )

    async def drain(self) -> None:
        """Emit coalesced progress until the owner sends an explicit completion sentinel."""
        if not self.callback:
            return
        while True:
            first = await self.queue.get()
            if first is None:
                return
            await asyncio.sleep(0.1)
            frames = [first]
            finished = False
            while not self.queue.empty():
                frame = self.queue.get_nowait()
                if frame is None:
                    finished = True
                    break
                frames.append(frame)
            for stream in ("stdout", "stderr"):
                selected = [frame for frame in frames if frame["stream"] == stream]
                if selected:
                    await self.callback(
                        {
                            "stream": stream,
                            "text": "".join(frame["text"] for frame in selected),
                            "truncated": any(frame["truncated"] for frame in selected),
                        }
                    )
            if finished:
                return

    async def finish(self, reader: asyncio.Task[None]) -> None:
        """Flush queued output before a final command receipt or cancellation is recorded."""
        self.queue.put_nowait(None)
        await asyncio.shield(reader)
