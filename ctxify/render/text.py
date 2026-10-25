"""Plain-text renderer: no markup at all."""

from __future__ import annotations

from collections.abc import Iterable

from ..ir import Block, Code, Data, Elision, Heading, KeyValue, ListBlock, Paragraph, Table
from ._tree import compact_json
from .md import one_line, table_width


def _pad(row: tuple[str, ...], width: int) -> list[str]:
    return list(row) + [""] * (width - len(row))


def _list(block: ListBlock, depth: int = 0) -> str:
    lines: list[str] = []
    for n, item in enumerate(block.items, 1):
        if isinstance(item, ListBlock):
            lines.append(_list(item, depth + 1))
        else:
            marker = f"{n}." if block.ordered else "-"
            lines.append(f"{'  ' * depth}{marker} {one_line(item)}")
    return "\n".join(lines)


def render_block(block: Block) -> str:
    """Render one block as plain text."""
    if isinstance(block, Heading):
        return one_line(block.text)
    if isinstance(block, Paragraph):
        return block.text.strip()
    if isinstance(block, ListBlock):
        return _list(block)
    if isinstance(block, Table):
        width = table_width(block)
        rows = ([block.header] if block.header is not None else []) + list(block.rows)
        body = "\n".join("\t".join(one_line(c) for c in _pad(r, width)) for r in rows)
        if block.caption:
            return f"{one_line(block.caption)}\n{body}".rstrip("\n")
        return body
    if isinstance(block, Code):
        return block.text.rstrip("\n")
    if isinstance(block, KeyValue):
        return "\n".join(f"{one_line(k)}: {one_line(v)}" for k, v in block.pairs)
    if isinstance(block, Data):
        return compact_json(block.value)
    if isinstance(block, Elision):
        return f"[... {block.count} more {block.what}]"
    raise TypeError(f"unknown block type: {type(block).__name__}")


def render_text(blocks: Iterable[Block]) -> str:
    """Render blocks as plain text, separated by blank lines."""
    parts = [p for p in (render_block(b) for b in blocks) if p]
    return "\n\n".join(parts) + "\n" if parts else ""
