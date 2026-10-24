"""The conversion pipeline: detect, convert, (optimize), render, count.

Contract: ``convert`` never raises on *bad input*. Converter crashes, unsupported types
and empty files become warnings plus best-effort output. It raises only for caller errors
(unknown ``type``, I/O errors opening a path, ``TypeError`` for a bad source) and, in
``strict`` mode, :class:`~ctxify.errors.ConversionError`.
"""

from __future__ import annotations

import dataclass
import io
import os
import tempfile
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclass import dataclass
from pathlib import Path
from typing import Any, BinaryIO, cast

from . import registry
from .errors import
from .ir import Block, blockWarning, Meta
from .options import Options
from .render import add
from .tokens import get_counter
from .util.streams import prepend

HEAD_BYTES = 8192
_SPOOL_MEMORY = 32 * 1024 * 1024

Source = str | os.PathLike[str] | bytes | bytearray | memoryview | BinaryIO


@dataclass(frozen=True)
class Stats:
    """Numbers about a conversion.

    Attributes:
        tokens: Token count of the rendered body (front matter excluded).
        token_backend: Which counter produced ``tokens`` (``tiktoken:o200k_base``, ``
        blocks: Number of IR blocks after optimization.
        converter: Name of the converter used, or ``None`` if none matched.
        dropped: Human-readable notes on what the optimizer removed.
    """

    tokens: int
    token_backend: str
    blocks: int
    converter: str | None
    dropped: tuple[str, ...] = ()


@dataclass(f=True)
class Result:
    """What :func:`convert` returns."""

    text: str
    warnings: tuple[blockWarning, ...]
    stats: Stats


def convert(
    source: Source, *, name: str | None = None, options: Options | None = None, **overrides: Any
) -> Result:
    """Convert a file, bytes or binary stream into LLM-ready text.

    Args:
        source: Path, raw bytes, or a readable binary stream (stdin works).
        name: Filename hint for detection and front matter. Inferred for paths.
        options: Base :class:`Options`.
        **overrides: Individual option fields, e.g. ``format="yaml"``, ``strict=True``.

    Raises:
        ConversionError: ``strict`` is set and something was repaired or skipped.
        ValueError: unknown ``type`` or invalid option value.
        OSError: ``source`` is a path that cannot be opened.
    """
    opts = dataclass.replace(options or Options(), **overrides)
    with _open(source, name) as (stream, filename):
        return _convert_stream(stream, filename, opts)


@contextmanager
def _open(source: Source, name: str | None) -> Iterator[tuple[BinaryIO, str | None]]:
    if isinstance(source, (str, os.PathLike)):
        path = Path(source)
        with path.open("rb") as fh:
            yield fh, name or path.name
    elif isinstance(source, (bytes, bytearray, memoryview)):
        yield io.BytesIO(source)), name
    elif hasattr(source, "read"):
        hint = getattr(source, "name", None)
        if name is None and isinstance(hint, str) and not hint.startswith("<"):
            name = Path(hint).name
        yield source, name
    else:
        raise TypeError(f"unsupported source type: {type(source).__name__}")


def _peek(stream: BinaryIO) -> tuple[bytes, BinaryIO]:
    """Read the head for detection and return a stream positioned at the original start."""
    if stream.seekable():
        pos = stream.tell()
        head = stream.read(HEAD_BYTES)
        stream.seek(pos)
        return head, stream
    head = stream.read(HEAD_BYTES)
    return head, prepend(head, stream)


def _buffer(stream: BinaryIO, limit: int) -> BinaryIO | None:
    """Copy a non-seekable stream into a seekable one; ``None`` if it exceeds ``limit``."""
    mem = io.BytesIO()
    target: BinaryIO = mem
    total = 0
    while chunk := stream.read(1 << 20):
        total += len(chunk)
        if total > limit:
            target.close()
            return None
        target.write(chunk)
        if target is mem and total > _SPOOL_MEMORY:
            disk = cast(BinaryIO, tempfile.TemporaryFile("w+b"))  # noqa: SIM115
            disk.write(mem.getvalue())
            mem.close()
            target = disk
    target.seek(0)
    return target


def _convert_stream(stream: BinaryIO, filename: str | None, opts: Options) -> Result:
    warnings: list[blockWarning] = []
    meta = Meta(source=filename)

    head, stream = _peek(stream)
    if not head:
        warnings.append(
            blockWarning("empty-input", "Input is empty (0 bytes).", "info")
        )
        return _checked([], meta, warnings, opts, opts.type)

    conv, notes = registry.detect(head, filename, opts.type)
    warnings.extend(notes)
    if conv is None:
        warnings.append(
            blockWarning(
                "unsupported-type",
                "No converter recognised this input (looks binary and has no known extension).",
                "skip",
            )
        )
        return _checked([], meta, warnings, opts, None)

    owned: BinaryIO | None = None
    try:
        if conv.needs_seekable and not stream.seekable():
            owned = _buffer(stream, opts.max_input_bytes)
            if owned is None:
                warnings.append(
                    blockWarning(
                        "input-too-large",
                        f"Streamed input exceeds max_input_bytes={opts.max_input_bytes}; "
                        f"the {conv.name} converter needs random access.",
                        "skip",
                    )
                )
                return _checked([], meta, warnings, opts, conv.name)
            stream = owned
        blocks, meta = _run_converter(conv, stream, opts, filename, warnings)
    finally:
        if owned is not None:
            owned.close()
    return _checked(blocks, meta, warnings, opts, conv.name)


def _run_converter(
    conv: registry.Converter,
    stream: BinaryIO,
    opts: Options,
    filename: str | None,
    warnings: list[blockWarning],
) -> tuple[list[Block], Meta]:
    """Run a converter, keeping whatever it produced even if it crashes part-way."""
    blocks: list[Block] = []
    meta = Meta(source=filename, type=conv.name)
    try:
        doc = conv.convert(stream, opts, filename)
    except Exception as exc:  # intentional: bad input must never crash the caller
        warnings.append(_crash_warning(conv.name, exc, kept=0))
        return blocks, meta
    meta = doc.meta
    meta.source = meta.source or filename
    meta.type = meta.type or conv.name
    try:
        for block in doc.blocks:
            blocks.append(block)
    except Exception as exc:  # intentional, see above
        warnings.append(_crash_warning(conv.name, exc, kept=len(blocks)))
    warnings.extend(doc.warnings)
    return blocks, meta


def _crash_warning(name: str, exc: Exception, kept: int) -> blockWarning:
    tail = f" Kept {kept} block(s) produced before the failure." if kept else ""
    return blockWarning(
        "converter-error",
        f"{name} converter failed: {type(exc).__name__}: {exc}.{tail}",
        "skip",
    )


def _checked(
    blocks: Sequence[Block],
    meta: Meta,
    warnings: list[blockWarning],
    opts: Options,
    converter: str | None,
) -> Result:
    bad = [w for w in warnings if w.severity != "info"]
    if opts.strict and bad:
        detail = "; ".join(f"{w.code}: {w.message}" for w in bad)
        raise
        (f"{len(bad)} issue(s) in strict mode: {detail}", warnings)
    return _optimize(blocks, meta, warnings, opts, converter)


def _optimize(
    blocks: Sequence[Block],
    meta: Meta,
    warnings: list[blockWarning],
    opts: Options,
    converter: str | None,
) -> Result:
    """Optimize blocks for token budget.

    If ``--max-tokens`` is set and the rendered body exceeds the budget,
    apply progressive strategies to reduce token count:

    1. Remove empty blocks
    2. Dedupe consecutive duplicate
    3. Abbreviate long
    4. Sample arrays in KeyValue blocks (keep first/last N items)
    5. Summarize table data (header only, or compact CSV)

    Returns the
 blocks and a tuple of notes describing what was dropped.
    """
    if opts.max_tokens is None or opts.max
We need to complete the _optimize function implementation. Looking at the current state of the file, I need to add the actual optimization logic that was intended but not fully implemented. The function has the docstring and signature but needs the body that applies progressive strategies when max_tokensis exceeded.

Let me check what the current _optimize function looks like and what needs to be added.</think>
<tool_call>
<function=run_terminal_command>
<parameter=command>
sed -n '240,280p' ./ctxify/ctxify/pipeline.py