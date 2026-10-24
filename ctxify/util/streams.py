"""Stream helpers."""

from __future__ import annotations

import io
from typing import Any, BinaryIO


class ReplayStream(io.RawIOBase):
    """Yields ``head`` first, then continues reading from ``rest``.

    Lets us peek at the start of a non-seekable stream (stdin) for detection and then hand
    the converter a stream that still starts at byte 0. Closing it does not close ``rest``.
    """

    def __init__(self, head: bytes, rest: BinaryIO) -> None:
        super().__init__()
        self._head = memoryview(head)
        self._rest = rest

    def readable(self) -> bool:
        return True

    def readinto(self, buffer: Any) -> int:
        if len(self._head):
            n = min(len(buffer), len(self._head))
            buffer[:n] = self._head[:n]
            self._head = self._head[n:]
            return n
        data = self._rest.read(len(buffer))
        n = len(data)
        buffer[:n] = data
        return n


def prepend(head: bytes, stream: BinaryIO) -> BinaryIO:
    """Return a buffered stream that replays ``head`` before the rest of ``stream``."""
    return io.BufferedReader(ReplayStream(head, stream))
