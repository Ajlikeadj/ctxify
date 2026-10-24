"""Renderers: IR blocks to text. Imported lazily per format."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from ..ir import Block


def render_body(blocks: Sequence[Block], fmt: str) -> str:
    """Render blocks in ``fmt`` (``md``, ``json-compact``, ``yaml`` or ``text``)."""
    if fmt == "md":
        from .md import render_md

        return render_md(blocks)
    if fmt == "text":
        from .text import render_text

        return render_text(blocks)
    if fmt == "json-compact":
        from .jsonc import render_json

        return render_json(blocks)
    if fmt == "yaml":
        from ._tree import content_obj
        from .yaml import dump

        return dump(content_obj(blocks))
    raise ValueError(f"unknown format {fmt!r}")


def add_front_matter(body: str, meta: dict[str, Any], fmt: str) -> str:
    """Attach metadata to an already-rendered body.

    ``md``/``text``: a ``---`` YAML header. ``yaml``: a two-document stream (meta, then
    content). ``json-compact``: ``{"meta":...,"content":...}``.
    """
    from ._tree import compact_json
    from .yaml import dump

    if fmt == "json-compact":
        return f'{{"meta":{compact_json(meta)},"content":{body.strip()}}}\n'
    if fmt == "yaml":
        return f"---\n{dump(meta)}---\n{body}"
    return f"---\n{dump(meta)}---\n\n{body}"
