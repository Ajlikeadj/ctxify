"""Conversion options shared by the library and the CLI."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, get_args

Format = Literal["md", "json-compact", "yaml", "text"]
TokenBackend = Literal["auto", "tiktoken", "chars"]

FORMATS: tuple[str, ...] = get_args(Format)
TOKEN_BACKENDS: tuple[str, ...] = get_args(TokenBackend)


@dataclass(frozen=True)
class Options:
    """Options for :func:`ctxify.convert`. Unknown keys raise ``TypeError``.

    Attributes:
        type: Force a converter by name (skips detection).
        format: Output format.
        front_matter: Prepend source/type/pages/token estimate/warnings.
        strict: Raise ``ConversionError`` instead of returning best-effort output
            when any warning has severity ``repair`` or ``skip``.
        token_backend: ``auto`` (tiktoken if usable, else chars/4), ``tiktoken``
            (raise if unusable) or ``chars``.
        max_input_bytes: Cap for buffering a non-seekable stream (stdin) when the
            converter needs random access (zip/PDF). Streaming converters ignore it.
    """

    type: str | None = None
    format: Format = "md"
    front_matter: bool = False
    strict: bool = False
    token_backend: TokenBackend = "auto"
    max_input_bytes: int = 1 << 30

    def __post_init__(self) -> None:
        if self.format not in FORMATS:
            raise ValueError(f"unknown format {self.format!r}; choose from {', '.join(FORMATS)}")
        if self.token_backend not in TOKEN_BACKENDS:
            raise ValueError(
                f"unknown token_backend {self.token_backend!r}; "
                f"choose from {', '.join(TOKEN_BACKENDS)}"
            )
        if self.max_input_bytes <= 0:
            raise ValueError("max_input_bytes must be positive")
