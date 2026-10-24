from __future__ import annotations

import io
from collections.abc import Iterator

import pytest

from ctxify import registry


class Pipe(io.RawIOBase):
    """A non-seekable binary stream, like stdin."""

    def __init__(self, data: bytes) -> None:
        super().__init__()
        self._b = io.BytesIO(data)

    def readable(self) -> bool:
        return True

    def readinto(self, b) -> int:  # type: ignore[no-untyped-def]
        data = self._b.read(len(b))
        b[: len(data)] = data
        return len(data)


@pytest.fixture
def pipe():
    return Pipe


@pytest.fixture
def custom() -> Iterator[list[str]]:
    """Registers converters for a test and removes them afterwards."""
    names: list[str] = []
    yield names
    for n in names:
        registry.unregister(n)
