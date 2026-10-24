"""Blocks to plain Python data (shared by the json-compact and yaml renderers)."""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from ..ir import Block, Code, Data, Elision, Heading, KeyValue, ListBlock, Paragraph, Table


def compact_json(value: object) -> str:
    """Deterministic, minimal-whitespace JSON that keeps non-ASCII characters as-is."""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def _list_items(block: ListBlock) -> list[Any]:
    return [block_to_obj(i) if isinstance(i, ListBlock) else i for i in block.items]


def block_to_obj(block: Block) -> Any:
    """Convert one block to JSON-like data. ``None``-valued fields are omitted."""
    if isinstance(block, Heading):
        return {"type": "heading", "level": block.level, "text": block.text}
    if isinstance(block, Paragraph):
        return {"type": "paragraph", "text": block.text}
    if isinstance(block, ListBlock):
        return {"type": "list", "ordered": block.ordered, "items": _list_items(block)}
    if isinstance(block, Table):
        obj: dict[str, Any] = {"type": "table"}
        if block.caption:
            obj["caption"] = block.caption
        if block.header is not None:
            obj["header"] = list(block.header)
        obj["rows"] = [list(r) for r in block.rows]
        return obj
    if isinstance(block, Code):
        code: dict[str, Any] = {"type": "code"}
        if block.lang:
            code["lang"] = block.lang
        code["text"] = block.text
        return code
    if isinstance(block, KeyValue):
        keys = [k for k, _ in block.pairs]
        if len(set(keys)) == len(keys):
            return {"type": "keyvalue", "pairs": dict(block.pairs)}
        return {"type": "keyvalue", "pairs": [list(p) for p in block.pairs]}
    if isinstance(block, Data):
        return {"type": "data", "value": block.value}
    if isinstance(block, Elision):
        return {"type": "elision", "count": block.count, "what": block.what}
    raise TypeError(f"unknown block type: {type(block).__name__}")


def content_obj(blocks: Sequence[Block]) -> Any:
    """A lone ``Data`` block is emitted as its bare value; otherwise a list of blocks."""
    if len(blocks) == 1 and isinstance(blocks[0], Data):
        return blocks[0].value
    return [block_to_obj(b) for b in blocks]
