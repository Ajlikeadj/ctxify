"""Markdown renderer. Whitespace-minimal on purpose: no column padding, one blank line
between blocks."""

from __future__ import annotations

import csv
import io
import re
from collections.abc import Iterable

from ..ir import Block, Code, Data, Elision, Heading, KeyValue, ListBlock, Paragraph, Table
from ._tree import compact_json


def fence_for(text: str) -> str:
    """A backtick fence longer than any backtick run inside ``text`` (minimum 3)."""
    longest = max((len(m) for m in re.findall(r"`+", text)), default=0)
    return "`" * max(3, longest + 1)


def one_line(text: str) -> str:
    """Collapse all whitespace (including newlines) to single spaces."""
    return " ".join(text.split())


def _cell(text: str) -> str:
    return one_line(text).replace("|", "\\|")


def table_width(table: Table) -> int:
    """Widest row or header; ragged rows are padded to this."""
    return max([len(table.header or ())] + [len(r) for r in table.rows], default=0)


def _pad(row: tuple[str, ...], width: int) -> list[str]:
    return list(row) + [""] * (width - len(row))


def _md_table(table: Table, width: int) -> str:
    header = _pad(table.header or (), width)
    lines = [
        "| " + " | ".join(_cell(c) for c in header) + " |",
        "|" + "|".join(["---"] * width) + "|",
    ]
    lines += ["| " + " | ".join(_cell(c) for c in _pad(r, width)) + " |" for r in table.rows]
    return "\n".join(lines)


def _csv_table(table: Table, width: int) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    if table.header is not None:
        writer.writerow([one_line(c) for c in _pad(table.header, width)])
    for row in table.rows:
        writer.writerow([one_line(c) for c in _pad(row, width)])
    body = buf.getvalue().rstrip("\n")
    fence = fence_for(body)
    return f"{fence}csv\n{body}\n{fence}"


def _records_table(table: Table, width: int) -> str:
    header = _pad(table.header or (), width)
    names = [h.strip() or f"col{i + 1}" for i, h in enumerate(header)]
    records = []
    for row in table.rows:
        # Empty cells are omitted entirely: a free saving that tables cannot make.
        lines = [
            f"{n}: {one_line(v)}" for n, v in zip(names, _pad(row, width), strict=True) if v.strip()
        ]
        if lines:
            records.append("\n".join(lines))
    return "\n\n".join(records)


def _table(table: Table) -> str:
    width = table_width(table)
    if width == 0:
        return ""
    render = {
        "markdown": _md_table,
        "csv": _csv_table,
        "records": _records_table,
    }.get(table.style, _md_table)
    body = render(table, width)
    return f"Table: {one_line(table.caption)}\n{body}" if table.caption else body


def _list(block: ListBlock, depth: int = 0) -> str:
    lines: list[str] = []
    indent = "   " * depth if block.ordered else "  " * depth
    for n, item in enumerate(block.items, 1):
        if isinstance(item, ListBlock):
            lines.append(_list(item, depth + 1))
            continue
        marker = f"{n}. " if block.ordered else "- "
        first, *rest = item.split("\n")
        lines.append(f"{indent}{marker}{first}")
        lines += [f"{indent}{' ' * len(marker)}{ln}" for ln in rest]
    return "\n".join(lines)


def render_block(block: Block) -> str:
    """Render one block (no trailing newline)."""
    if isinstance(block, Heading):
        return f"{'#' * min(max(block.level, 1), 6)} {one_line(block.text)}"
    if isinstance(block, Paragraph):
        return block.text.strip()
    if isinstance(block, ListBlock):
        return _list(block)
    if isinstance(block, Table):
        return _table(block)
    if isinstance(block, Code):
        text = block.text.rstrip("\n")
        fence = fence_for(text)
        return f"{fence}{block.lang or ''}\n{text}\n{fence}"
    if isinstance(block, KeyValue):
        return "\n".join(f"{one_line(k)}: {one_line(v)}" for k, v in block.pairs)
    if isinstance(block, Data):
        return f"```json\n{compact_json(block.value)}\n```"
    if isinstance(block, Elision):
        return f"[... {block.count} more {block.what}]"
    raise TypeError(f"unknown block type: {type(block).__name__}")


def render_md(blocks: Iterable[Block]) -> str:
    """Render blocks as Markdown. Empty input gives an empty string."""
    parts = [p for p in (render_block(b) for b in blocks) if p]
    return "\n\n".join(parts) + "\n" if parts else ""
