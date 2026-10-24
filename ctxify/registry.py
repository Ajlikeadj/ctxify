"""Converter registry and format detection.

Detection scores every registered converter and picks the highest score:

* ``2.0``: magic bytes match (strongest evidence)
* ``1.0``: file extension matches
* ``0.0 < s < 1.0``: weak content sniff (the text converter's catch-all)
* ``0.0``: not mine

Ties go to the converter registered first. Built-ins load lazily on first use, and
third-party plugins (entry-point group ``ctxify.converters``) are only consulted when no
built-in recognises the input, so the common path never pays for ``importlib.metadata``.
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, BinaryIO, Protocol, runtime_checkable

from .ir import ConversionWarning, Document

if TYPE_CHECKING:
    from .options import Options

MAGIC = 2.0
EXTENSION = 1.0


@runtime_checkable
class Converter(Protocol):
    """Interface every format implements.

    Attributes:
        name: Unique type name (used by ``--type``).
        extensions: Lowercase extensions including the dot, e.g. ``(".csv", ".tsv")``.
        needs_seekable: True if ``convert`` needs ``seek()`` (zip/PDF). The pipeline then
            buffers non-seekable input (stdin) before calling it.
    """

    name: str
    extensions: tuple[str, ...]
    needs_seekable: bool

    def detect(self, head: bytes, filename: str | None) -> float:
        """Score how likely this converter handles the input (see module docstring)."""
        ...

    def convert(self, stream: BinaryIO, opts: Options, filename: str | None) -> Document:
        """Parse ``stream`` into IR. Must not raise on bad input: repair and warn instead."""
        ...


# (name, "module:Class"), in tie-break order. Modules must be cheap to import: heavy
# third-party imports belong inside ``convert``.
_BUILTINS: tuple[tuple[str, str], ...] = (
    ("text", "ctxify.converters.text:TextConverter"),
    ("csv", "ctxify.converters.csv:CsvConverter"),
    ("docx", "ctxify.converters.docx:DocxConverter"),
)

_converters: dict[str, Converter] = {}
_builtins_loaded = False
_plugins_loaded = False


def register(converter: Converter, *, replace: bool = False) -> None:
    """Add a converter. Raises ``ValueError`` on a duplicate name unless ``replace``."""
    if converter.name in _converters and not replace:
        raise ValueError(f"converter {converter.name!r} is already registered")
    _converters[converter.name] = converter


def unregister(name: str) -> None:
    """Remove a converter (no error if absent)."""
    _converters.pop(name, None)


def _ensure_builtins() -> None:
    global _builtins_loaded
    if _builtins_loaded:
        return
    _builtins_loaded = True
    for name, target in _BUILTINS:
        if name in _converters:
            continue
        module_name, _, attr = target.partition(":")
        cls = getattr(importlib.import_module(module_name), attr)
        register(cls())


def _ensure_plugins(notes: list[ConversionWarning]) -> None:
    global _plugins_loaded
    if _plugins_loaded:
        return
    _plugins_loaded = True
    from importlib.metadata import entry_points

    for ep in sorted(entry_points(group="ctxify.converters"), key=lambda e: e.name):
        try:
            obj = ep.load()
            register(obj() if isinstance(obj, type) else obj)
        except Exception as exc:
            notes.append(
                ConversionWarning(
                    "plugin-error", f"plugin {ep.name!r} failed to load: {exc}", "repair"
                )
            )


def converters() -> list[Converter]:
    """All registered converters, in tie-break order."""
    _ensure_builtins()
    return list(_converters.values())


def get(name: str) -> Converter | None:
    """Look up a converter by name (built-ins only; plugins load during :func:`detect`)."""
    _ensure_builtins()
    return _converters.get(name)


def _best(head: bytes, filename: str | None, notes: list[ConversionWarning]) -> Converter | None:
    best: Converter | None = None
    best_score = 0.0
    for conv in _converters.values():
        try:
            score = conv.detect(head, filename)
        except Exception as exc:  # a buggy plugin must not break detection
            notes.append(
                ConversionWarning("detect-error", f"{conv.name}.detect failed: {exc}", "repair")
            )
            continue
        if score > best_score:  # strict '>' keeps the earliest-registered on ties
            best, best_score = conv, score
    return best


def detect(
    head: bytes, filename: str | None, forced: str | None = None
) -> tuple[Converter | None, list[ConversionWarning]]:
    """Choose a converter for the input.

    Args:
        head: First bytes of the input (the pipeline passes up to 8 KiB).
        filename: Basename used for the extension check, if known.
        forced: Converter name from ``--type``; skips scoring.

    Returns:
        ``(converter or None, notes)``. ``notes`` are warnings raised during detection.

    Raises:
        ValueError: ``forced`` names an unknown converter.
    """
    _ensure_builtins()
    notes: list[ConversionWarning] = []
    if forced is not None:
        conv = _converters.get(forced)
        if conv is None:
            _ensure_plugins(notes)
            conv = _converters.get(forced)
        if conv is None:
            raise ValueError(
                f"unknown type {forced!r}; available: {', '.join(sorted(_converters))}"
            )
        return conv, notes
    conv = _best(head, filename, notes)
    if conv is None and not _plugins_loaded:
        _ensure_plugins(notes)
        conv = _best(head, filename, notes)
    return conv, notes
