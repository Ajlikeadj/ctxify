"""The conversion pipeline: detect, convert, optimize, render, count.

Contract: ``convert`` never raises on *bad input*. Converter crashes, unsupported types
and empty files become warnings plus best-effort output. It raises only for caller errors
(unknown ``type``, I/O errors opening a path, ``TypeError`` for a bad source), a missing
explicitly requested tokenizer, and, in ``strict`` mode,
:class:`~ctxify.errors.ConversionError`.
"""

from __future__ import annotations

import io
import os
import re
import tempfile
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, BinaryIO, cast

from . import registry
from .errors import ConversionError
from .ir import (
    Block,
    ConversionWarning,
    Elision,
    KeyValue,
    ListBlock,
    Meta,
    Paragraph,
    Table,
)
from .options import Options
from .render import add_front_matter, render_body
from .tokens import TokenCounter, get_counter
from .util.streams import prepend

HEAD_BYTES = 8192
_SPOOL_MEMORY = 32 * 1024 * 1024
_TABLE_SAMPLE_ROWS = 20
_KV_SAMPLE_PAIRS = 30
_TABLE_SAMPLE_THRESHOLD = 30
_KV_SAMPLE_THRESHOLD = 40
_TRUNCATION_MARKER = "\n[... truncated]"

Source = str | os.PathLike[str] | bytes | bytearray | memoryview | BinaryIO


@dataclass(frozen=True)
class Stats:
    """Numbers about a conversion.

    Attributes:
        tokens: Token count of the rendered body (front matter excluded).
        token_backend: Which counter produced ``tokens`` (``tiktoken:o200k_base``,
            ``chars/4``).
        blocks: Number of IR blocks after optimization.
        converter: Name of the converter used, or ``None`` if none matched.
        dropped: Human-readable notes on what the optimizer removed.
    """

    tokens: int
    token_backend: str
    blocks: int
    converter: str | None
    dropped: tuple[str, ...] = ()


@dataclass
class Result:
    """What :func:`convert` returns."""

    text: str
    warnings: tuple[ConversionWarning, ...]
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
        TypeError: ``source`` is not a path, bytes or readable stream.
        OSError: ``source`` is a path that cannot be opened.
        TokenizerUnavailable: ``token_backend="tiktoken"`` but tiktoken is unusable.
    """
    opts = replace(options or Options(), **overrides)
    with _open(source, name) as (stream, filename):
        return _convert_stream(stream, filename, opts)


@contextmanager
def _open(source: Source, name: str | None) -> Iterator[tuple[BinaryIO, str | None]]:
    if isinstance(source, (str, os.PathLike)):
        path = Path(source)
        with path.open("rb") as fh:
            yield fh, name or path.name
    elif isinstance(source, (bytes, bytearray, memoryview)):
        yield io.BytesIO(bytes(source)), name
    elif hasattr(source, "read"):
        hint = getattr(source, "name", None)
        if name is None and isinstance(hint, str) and not hint.startswith("<"):
            name = Path(hint).name
        yield source, name
    else:
        raise TypeError(f"unsupported source type: {type(source).__name__}")


def _peek(stream: BinaryIO) -> tuple[bytes, BinaryIO]:
    """Read the head for detection and return a stream positioned at the original start.

    Some streams (notably stdin pipes on Windows) report ``seekable()`` but lose buffered
    data across ``seek()``. We verify the rewind with a one-byte probe and fall back to a
    replaying stream when the round-trip is not faithful.
    """
    try:
        seekable = bool(stream.seekable())
    except Exception:
        seekable = False
    if seekable:
        try:
            pos = stream.tell()
            head = stream.read(HEAD_BYTES)
            stream.seek(pos)
            if not head or stream.read(1) == head[:1]:
                stream.seek(pos)
                return head, stream
            stream.seek(pos)
            return head, prepend(head, stream)
        except (OSError, ValueError):
            pass
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
    warnings: list[ConversionWarning] = []
    meta = Meta(source=filename)

    head, stream = _peek(stream)
    if not head:
        warnings.append(ConversionWarning("empty-input", "Input is empty (0 bytes).", "info"))
        return _checked([], meta, warnings, opts, opts.type)

    conv, notes = registry.detect(head, filename, opts.type)
    warnings.extend(notes)
    if conv is None:
        warnings.append(
            ConversionWarning(
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
                    ConversionWarning(
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
    warnings: list[ConversionWarning],
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
        # Exhaust the generator *before* reading warnings (see ir module docstring).
        for block in doc.blocks:
            blocks.append(block)
    except Exception as exc:  # intentional, see above
        warnings.append(_crash_warning(conv.name, exc, kept=len(blocks)))
    warnings.extend(doc.warnings)
    return blocks, meta


def _crash_warning(name: str, exc: Exception, kept: int) -> ConversionWarning:
    tail = f" Kept {kept} block(s) produced before the failure." if kept else ""
    return ConversionWarning(
        "converter-error",
        f"{name} converter failed: {type(exc).__name__}: {exc}.{tail}",
        "skip",
    )


def _checked(
    blocks: Sequence[Block],
    meta: Meta,
    warnings: list[ConversionWarning],
    opts: Options,
    converter: str | None,
) -> Result:
    bad = [w for w in warnings if w.severity != "info"]
    if opts.strict and bad:
        detail = "; ".join(f"{w.code}: {w.message}" for w in bad)
        raise ConversionError(f"{len(bad)} issue(s) in strict mode: {detail}", warnings)
    return _optimize(blocks, meta, warnings, opts, converter)


def _optimize(
    blocks: Sequence[Block],
    meta: Meta,
    warnings: list[ConversionWarning],
    opts: Options,
    converter: str | None,
) -> Result:
    """Resolve table styles, apply the token budget, render, and attach front matter."""
    optimized = _resolve_styles(list(blocks))
    counter = get_counter(opts.token_backend)
    body = render_body(optimized, opts.format)
    dropped: list[str] = []

    if opts.max_tokens is not None:
        body, optimized, dropped = _reduce(optimized, opts, counter, opts.max_tokens)
        dropped = list(dict.fromkeys(dropped))

    tokens = counter.count(body)
    meta_obj: dict[str, Any] = {"source": meta.source, "type": meta.type}
    if meta.pages is not None:
        meta_obj["pages"] = meta.pages
    meta_obj["tokens"] = tokens
    meta_obj["token_backend"] = counter.name
    if warnings:
        meta_obj["warnings"] = [f"{w.code}: {w.message}" for w in warnings]
    if dropped:
        meta_obj["dropped"] = dropped

    text = add_front_matter(body, meta_obj, opts.format) if opts.front_matter else body
    return Result(
        text=text,
        warnings=tuple(warnings),
        stats=Stats(
            tokens=tokens,
            token_backend=counter.name,
            blocks=len(optimized),
            converter=converter,
            dropped=tuple(dropped),
        ),
    )


# ── Table styles ───────────────────────────────────────────────────────────


def _estimate(table: Table, style: str) -> int:
    """Cheap character estimate for one table style (no rendering required)."""
    header = table.header or ()
    width = max([len(header)] + [len(r) for r in table.rows], default=0)
    if style == "csv":
        return sum(len(c) + 1 for c in header) + 1 + sum(
            sum(len(c) + 1 for c in row) + 1 for row in table.rows
        )
    if style == "records":
        names = [h.strip() or f"col{i + 1}" for i, h in enumerate(header)]
        total = 0
        for row in table.rows:
            for name, cell in zip(names, row, strict=False):
                if cell.strip():
                    total += len(name) + len(cell) + 4
            total += 2
        return total
    return sum(len(c) for c in header) + 4 * width + sum(
        sum(len(c) for c in row) + 4 * width for row in table.rows
    )


def _pick_style(table: Table) -> str:
    """Pick a representation: records for sparse data, markdown when small, CSV when big."""
    width = max([len(table.header or ())] + [len(r) for r in table.rows], default=0)
    rows = len(table.rows)
    cells = rows * width
    filled = sum(1 for row in table.rows for cell in row if cell.strip())
    if cells and rows > 3 and filled / cells < 0.4:
        return "records"
    if rows <= 10 and width <= 8:
        return "markdown"
    if rows <= 20:
        return min(("markdown", "csv"), key=lambda s: _estimate(table, s))
    return "csv"


def _resolve_styles(blocks: list[Block]) -> list[Block]:
    out: list[Block] = []
    for block in blocks:
        if isinstance(block, Table) and block.style == "auto":
            out.append(replace(block, style=_pick_style(block)))  # type: ignore[arg-type]
        else:
            out.append(block)
    return out


# ── Token budget ───────────────────────────────────────────────────────────


def _is_empty(block: Block) -> bool:
    if isinstance(block, Paragraph):
        return not block.text.strip()
    if isinstance(block, KeyValue):
        return not block.pairs
    if isinstance(block, ListBlock):
        return not block.items
    if isinstance(block, Table):
        return block.header is None and not block.rows
    return False


def _abbreviate_text(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _abbreviate(block: Block) -> tuple[Block, bool]:
    if isinstance(block, Table):
        changed = False
        rows: list[tuple[str, ...]] = []
        for row in block.rows:
            cells: list[str] = []
            for cell in row:
                short = _abbreviate_text(cell, 60) if len(cell) > 60 else cell
                changed |= short != cell
                cells.append(short)
            rows.append(tuple(cells))
        header = None
        if block.header is not None:
            hdr: list[str] = []
            for cell in block.header:
                short = _abbreviate_text(cell, 60) if len(cell) > 60 else cell
                changed |= short != cell
                hdr.append(short)
            header = tuple(hdr)
        return replace(block, header=header, rows=tuple(rows)), changed
    if isinstance(block, KeyValue):
        changed = False
        pairs: list[tuple[str, str]] = []
        for key, value in block.pairs:
            short = _abbreviate_text(value, 120) if len(value) > 120 else value
            changed |= short != value
            pairs.append((key, short))
        return replace(block, pairs=tuple(pairs)), changed
    if isinstance(block, ListBlock):
        changed = False
        items: list[str | ListBlock] = []
        for item in block.items:
            if isinstance(item, ListBlock):
                nested, nested_changed = _abbreviate(item)
                items.append(nested)  # type: ignore[arg-type]
                changed |= nested_changed
            else:
                short = _abbreviate_text(item, 120) if len(item) > 120 else item
                changed |= short != item
                items.append(short)
        return replace(block, items=tuple(items)), changed
    return block, False


def _sample_tables(blocks: list[Block]) -> tuple[list[Block], int]:
    out: list[Block] = []
    dropped_rows = 0
    for block in blocks:
        if isinstance(block, Table) and len(block.rows) > _TABLE_SAMPLE_THRESHOLD:
            kept = block.rows[:_TABLE_SAMPLE_ROWS]
            dropped_rows += len(block.rows) - len(kept)
            out.append(replace(block, rows=kept))
            out.append(Elision(len(block.rows) - len(kept), "table rows"))
        elif isinstance(block, KeyValue) and len(block.pairs) > _KV_SAMPLE_THRESHOLD:
            kept_pairs = block.pairs[:_KV_SAMPLE_PAIRS]
            out.append(replace(block, pairs=kept_pairs))
            out.append(Elision(len(block.pairs) - len(kept_pairs), "key/value pairs"))
        else:
            out.append(block)
    return out, dropped_rows


def _compact_tables(blocks: list[Block]) -> tuple[list[Block], int]:
    return [
        replace(b, style="csv") if isinstance(b, Table) else b for b in blocks
    ], sum(1 for b in blocks if isinstance(b, Table))


def _header_only_tables(blocks: list[Block]) -> tuple[list[Block], int]:
    out: list[Block] = []
    dropped_rows = 0
    for block in blocks:
        if isinstance(block, Table) and block.rows:
            out.append(replace(block, header=block.header or (), rows=()))
            out.append(Elision(len(block.rows), "table rows"))
            dropped_rows += len(block.rows)
        else:
            out.append(block)
    return out, dropped_rows


def _open_fence(text: str) -> str | None:
    """The fence marker of an unterminated code block, if any."""
    opening: str | None = None
    for match in re.finditer(r"^(`{3,})[^\n]*$", text, re.M):
        if opening is None:
            opening = match.group(1)
        elif match.group(1) == opening:
            opening = None
    return opening


def _truncate(body: str, limit: int, counter: TokenCounter) -> tuple[str, bool]:
    """Cut ``body`` to at most ``limit`` tokens, keeping as much text as fits.

    A cut that lands inside a fenced code block is repaired: close the fence when the
    budget allows, otherwise drop the unterminated block so the output stays well-formed.
    """
    if counter.count(body) <= limit:
        return body, False
    low, high = 0, len(body)
    while low < high:
        mid = (low + high + 1) // 2
        if counter.count(body[:mid]) <= limit:
            low = mid
        else:
            high = mid - 1

    head = body[:low].rstrip()
    fence = _open_fence(head)
    if fence is not None:
        closed = f"{head}\n{fence}"
        if counter.count(f"{closed}{_TRUNCATION_MARKER}") <= limit:
            return f"{closed}{_TRUNCATION_MARKER}", True
        if counter.count(closed) <= limit:
            return closed, True
        start = head.rfind("\n```")
        head = head[:start].rstrip() if start >= 0 else ""

    marked = f"{head}{_TRUNCATION_MARKER}" if head else ""
    if marked and counter.count(marked) <= limit:
        return marked, True
    return head, True


def _drop_tail(
    blocks: list[Block], opts: Options, counter: TokenCounter, limit: int
) -> tuple[list[Block], int]:
    """Longest prefix of blocks that fits the budget (keeps output well-formed)."""
    low, high = 0, len(blocks)
    best = 0
    while low <= high:
        mid = (low + high) // 2
        if counter.count(render_body(blocks[:mid], opts.format)) <= limit:
            best = mid
            low = mid + 1
        else:
            high = mid - 1
    return blocks[:best], len(blocks) - best


def _reduce(
    blocks: list[Block], opts: Options, counter: TokenCounter, limit: int
) -> tuple[str, list[Block], list[str]]:
    """Progressive reduction until the rendered body fits ``limit`` tokens."""
    notes: list[str] = []

    def measure(candidate: list[Block]) -> tuple[str, int]:
        rendered = render_body(candidate, opts.format)
        return rendered, counter.count(rendered)

    body, tokens = measure(blocks)
    if tokens <= limit:
        return body, blocks, notes

    # 1. Empty blocks and consecutive duplicates.
    pruned: list[Block] = []
    empty = duplicates = 0
    for block in blocks:
        if _is_empty(block):
            empty += 1
            continue
        if pruned and pruned[-1] == block:
            duplicates += 1
            continue
        pruned.append(block)
    if empty:
        notes.append(f"removed {empty} empty block(s)")
    if duplicates:
        notes.append(f"deduplicated {duplicates} consecutive block(s)")
    blocks = pruned
    body, tokens = measure(blocks)
    if tokens <= limit:
        return body, blocks, notes

    # 2. Abbreviate long cell and value strings.
    abbreviated: list[Block] = []
    changed = 0
    for block in blocks:
        short, did = _abbreviate(block)
        abbreviated.append(short)
        changed += int(did)
    if changed:
        notes.append(f"abbreviated {changed} block(s)")
    blocks = abbreviated
    body, tokens = measure(blocks)
    if tokens <= limit:
        return body, blocks, notes

    # 3. Sample oversized tables and key/value lists.
    blocks, dropped_rows = _sample_tables(blocks)
    if dropped_rows:
        notes.append(f"sampled tables, dropped {dropped_rows} row(s)")
    body, tokens = measure(blocks)
    if tokens <= limit:
        return body, blocks, notes

    # 4. Render every table as compact CSV.
    blocks, tables = _compact_tables(blocks)
    if tables:
        notes.append(f"switched {tables} table(s) to compact CSV")
    body, tokens = measure(blocks)
    if tokens <= limit:
        return body, blocks, notes

    # 5. Keep table headers only.
    blocks, dropped_rows = _header_only_tables(blocks)
    if dropped_rows:
        notes.append(f"kept table headers only, dropped {dropped_rows} row(s)")
    body, tokens = measure(blocks)
    if tokens <= limit:
        return body, blocks, notes

    # 6. Drop whole trailing blocks so the remaining output stays well-formed.
    full_body = body
    kept, dropped_blocks = _drop_tail(blocks, opts, counter, limit)
    if dropped_blocks and kept:
        notes.append(f"dropped {dropped_blocks} trailing block(s)")
        return render_body(kept, opts.format), kept, notes

    # 7. Hard truncation of the single oversized block.
    body, truncated = _truncate(full_body, limit, counter)
    if truncated:
        notes.append(f"truncated output to {limit} tokens")
    return body, blocks, notes
