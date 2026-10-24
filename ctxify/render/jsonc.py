"""json-compact renderer."""

from __future__ import annotations

from collections.abc import Sequence

from ..ir import Block
from ._tree import compact_json, content_obj


def render_json(blocks: Sequence[Block]) -> str:
    """Render blocks as one line of compact JSON (a bare value for lone ``Data``)."""
    return compact_json(content_obj(blocks)) + "\n"
