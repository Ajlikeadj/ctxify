"""Exception types."""

from __future__ import annotations

from collections.abc import Iterable

from .ir import ConversionWarning


class ConversionError(Exception):
    """Raised in ``strict`` mode when a conversion needed repairs or skipped content.

    Attributes:
        warnings: Every warning collected before the failure was raised.
    """

    def __init__(self, message: str, warnings: Iterable[ConversionWarning] = ()) -> None:
        super().__init__(message)
        self.warnings: tuple[ConversionWarning, ...] = tuple(warnings)
