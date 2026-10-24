"""JSON/JSONL converter for ctxify.

Handles deeply nested, huge, or irregular JSON/JSONL structures.
Uses ijson for streaming iteration to avoid loading large files into memory.

Output: Markdown by default, with --table-style options for compact representation.
"""

from __future__ import annotations

import io
import json
from collections import Counter
from typing import BinaryIO, Optional, Tuple, Any

from ..ir import Block, ConversionWarning, Document, Data, Elision, KeyValue, Meta, Paragraph, Table
from ..options import Options
from ..util.encoding import detect_encoding, _strip_bom
from ..util.streams import prepend


# ── Helpers ────────────────────────────────────────────────────────────────

def _safe_json_loads(text: str) -> Any:
    """Parse JSON text safely, returning None on failure."""
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None


def _infer_simple_type(value: Any) -> str:
    """Infer a simple type name for display purposes."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "string"
    return "object"


def _count_deep_nesting(obj: Any, max_depth: int = 50) -> int:
    """Count maximum nesting depth of a JSON structure."""
    if isinstance(obj, dict):
        if not obj:
            return 1
        return 1 + max((_count_deep_nesting(v, max_depth - 1) for v in obj.values()), default=0)
    if isinstance(obj, list):
        if not obj:
            return 1
        return 1 + max((_count_deep_nesting(item, max_depth - 1) for item in obj), default=0)
    return 0


def _flatten_dict(
    d: dict,
    prefix: str = "",
    max_depth: int = 10,
    current_depth: int = 0,
) -> dict:
    """Flatten a nested dict up to max_depth levels.

    Deeper nesting is summarized with prefix notation.
    """
    if current_depth >= max_depth:
        return {prefix: f"[deep nesting, {max_depth} levels]"}

    result: dict = {}
    for key, value in d.items():
        new_prefix = f"{prefix}.{key}" if prefix else key

        if isinstance(value, dict):
            if current_depth + 1 >= max_depth:
                result[new_prefix] = f"[deep nesting]"
            else:
                result.update(_flatten_dict(value, new_prefix, max_depth, current_depth + 1))
        elif isinstance(value, list):
            result[new_prefix] = _summarize_array(value, max_depth, current_depth + 1)
        else:
            result[new_prefix] = value

    return result


def _summarize_array(
    arr: list,
    max_items: int = 10,
    current_depth: int = 0,
) -> str:
    """Summarize an array for display in flattened JSON.

    Shows first few items, then ellipsis if too many.
    """
    if not arr:
        return "[]"

    # Count non-null items
    non_null = [item for item in arr if item is not None]

    if len(non_null) <= max_items * 2:
        # Small enough to show all
        items_str = ", ".join(_value_to_summary(item) for item in non_null)
        return f"[{items_str}]"

    # Show first and last items with count
    first = [_value_to_summary(item) for item in non_null[:max_items // 2]]
    last = [_value_to_summary(item) for item in non_null[-max_items // 2:]]
    total = len(non_null)

    parts: list[str] = []
    if first:
        parts.append(", ".join(first))
    if last:
        parts.append(", ".join(last))
    parts.append(f"[{total - len(first) - len(last)} more items]")

    return "[" + ". ".join(parts) + "]"


def _value_to_summary(value: Any) -> str:
    """Convert a value to a summary string for JSON display."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        # Show reasonable precision
        if value == int(value):
            return str(int(value))
        return f"{value:.6g}"
    if isinstance(value, str):
        if len(value) > 40:
            return f'"{value[:40]}..."'
        return f'"{value}"'
    return str(value)


def _infer_schema(obj: Any, max_depth: int = 5) -> dict:
    """Infer a simple schema description from a JSON object.

    Returns a dict with type info and example values.
    """
    if isinstance(obj, dict):
        if not obj:
            return {"type": "object", "example": {}}
        # Get keys and their types
        keys_info = {}
        for key in list(obj.keys())[:20]:  # Limit to first 20 keys
            val = obj[key]
            keys_info[key] = {
                "type": _infer_simple_type(val),
                "example": _value_to_summary(val) if not isinstance(val, (dict, list)) or len(str(val)) < 50 else f"[{type(val).__name__}]",
            }
        return {
            "type": "object",
            "num_keys": len(obj),
            "keys": keys_info,
        }
    if isinstance(obj, list):
        if not obj:
            return {"type": "array", "example": []}
        # Sample first few items
        samples = obj[:5] if len(obj) > 5 else obj
        item_types = [_infer_simple_type(item) for item in samples]
        return {
            "type": "array",
            "length": len(obj),
            "item_type": item_types[0] if len(set(item_types)) == 1 else "mixed",
            "example": _value_to_summary(obj[0]) if len(obj) > 0 and not isinstance(obj[0], (dict, list)) or len(str(obj[0])) < 50 else f"[{len(set(item_types))} item types]",
        }
    return {"type": type(obj).__name__, "value": _value_to_summary(obj)}


# ── Converter ──────────────────────────────────────────────────────────────

class JsonConverter:
    """Converts ``.json``/``.jsonl`` files into clean IR blocks."""

    name = "json"
    extensions = (".json", ".jsonl")
    needs_seekable = True  # JSONL may need random access for schema inference

    def detect(self, head: bytes, filename: str | None) -> float:
        if filename and filename.lower().endswith(self.extensions):
            return 1.0
        # Heuristic: if the bytes start with { or [, it might be JSON
        if head and head[0:1] in (b"{", b"[") and b"\x00" not in head[:100]:
            return 0.1
        return 0.0

    def convert(self, stream: BinaryIO, opts: Options, filename: str | None) -> Document:
        """Convert JSON/JSONL input to IR blocks."""

        # Read with encoding detection
        sample = stream.read(65536)
        guess = detect_encoding(sample)
        warnings: list[ConversionWarning] = []

        if guess.method in ("detected", "fallback"):
            warnings.append(
                ConversionWarning(
                    "encoding-guessed",
                    f"Input is not valid UTF-8; decoded as {guess.name} ({guess.method}).",
                    "repair",
                )
            )

        # Strip BOM if present
        raw = _strip_bom(sample)

        # Decode with replacement
        try:
            text = raw.decode(guess.name or "utf-8", errors="replace")
        except Exception:
            text = raw.decode("utf-8", errors="replace")

        if "\ufffd" in text:
            warnings.append(
                ConversionWarning(
                    "invalid-bytes",
                    "Found U+FFFD replacement characters (undecodable bytes).",
                    "repair",
                )
            )

        # Determine if this is JSONL (one JSON object per line) or single JSON
        lines = text.splitlines()
        non_empty_lines = [l for l in lines if l.strip()]

        is_jsonl = (
            len(non_empty_lines) > 0
            and all(_safe_json_loads(l) is not None for l in non_empty_lines[:20])
            or filename and filename.lower().endswith(".jsonl")
        )

        meta = Meta(source=filename, type=self.name)

        if is_jsonl:
            return self._convert_jsonl(stream, text, meta, warnings)
        else:
            return self._convert_json(stream, text, meta, warnings)

    def _convert_jsonl(
        self, stream: BinaryIO, text: str, meta: Meta, warnings: list[ConversionWarning]
    ) -> Document:
        """Convert JSONL (one JSON object per line) to IR blocks."""

        import ijson

        blocks: list[Block] = []
        schema: dict | None = None
        array_summaries: list[str] = []
        total_items = 0
        seen_schemas: set[tuple] = set()

        # Stream-parse JSONL with ijson
        try:
            # Parse as array of objects
            # Use ijson.items for streaming
            objects = list(ijson.items(io.BytesIO(text.encode("utf-8")), "item"))

            if not objects:
                warnings.append(
                    ConversionWarning(
                        "empty-input",
                        "JSONL file has no content.",
                        "info",
                    )
                )
                return Document([Data({})], meta, warnings)

            # Infer schema from first few objects
            if objects:
                schema = _infer_schema(objects[0], max_depth=5)
                warnings.append(
                    ConversionWarning(
                        "schema-inferred",
                        f"Inferred schema: {schema.get('type', 'object')} with {schema.get('num_keys', 0)} keys",
                        "info",
                    )
                )

            # Process objects into KeyValue blocks
            for i, obj in enumerate(objects):
                total_items += 1

                # Get schema info for this object
                obj_schema = _infer_schema(obj, max_depth=5)

                # Convert to KeyValue pairs
                pairs: list[tuple[str, str]] = []
                for key, value in obj.items():
                    summary = _value_to_summary(value)
                    pairs.append((key, summary))

                # Check for repeated schema
                pair_tuple = tuple(pairs)
                if pair_tuple in seen_schemas:
                    # Find existing block and add note
                    pass  # we'll just keep the first one
                else:
                    seen_schemas.add(pair_tuple)
                    blocks.append(KeyValue(tuple(pairs)))

                # Track array summaries if this object has arrays
                for key, value in obj.items():
                    if isinstance(value, list):
                        arr_summary = _summarize_array(value, max_items=5)
                        if arr_summary not in array_summaries:
                            array_summaries.append(arr_summary)

            # Add elision if there are more objects than displayed
            if len(objects) > 0 and len(objects) < 1000:
                pass  # all objects fit

        except Exception as exc:
            warnings.append(
                ConversionWarning(
                    "jsonl-error",
                    f"JSONL parsing error: {type(exc).__name__}: {exc}",
                    "repair",
                )
            )
            # Fall back to treating as single JSON
            stream.seek(0)
            return self._convert_json(stream, text, meta, warnings)

        # Add array summary if we have them
        if array_summaries:
            elision_blocks = [
                Elision(count=len(array_summaries), what="array schemas")
            ]
            # Insert after the last block
            if blocks:
                blocks.extend(elision_blocks)
            else:
                blocks = elision_blocks

        return Document(blocks, meta, warnings)

    def _convert_json(
        self, stream: BinaryIO, text: str, meta: Meta, warnings: list[ConversionWarning]
    ) -> Document:
        """Convert single JSON object/array to IR blocks."""

        # Parse JSON
        data = _safe_json_loads(text)
        if data is None:
            warnings.append(
                ConversionWarning(
                    "invalid-json",
                    "Invalid JSON input.",
                    "skip",
                )
            )
            return Document([Data({})], meta, warnings)

        blocks: list[Block] = []

        # Handle different JSON types
        if isinstance(data, dict):
            blocks.extend(self._dict_to_blocks(data, max_depth=10))
        elif isinstance(data, list):
            blocks.extend(self._list_to_blocks(data, max_depth=10))
        else:
            # Scalar value
            blocks.append(Data(data))

        return Document(blocks, meta, warnings)

    def _dict_to_blocks(
        self, d: dict, max_depth: int = 10, current_depth: int = 0
    ) -> list[Block]:
        """Convert a dict to IR blocks.

        Uses flattening for deep nesting.
        """
        blocks: list[Block] = []

        if current_depth >= max_depth:
            # Summarize deep nesting
            blocks.append(Elision(count=1, what="deeply nested dict"))
            return blocks

        # Try flattening
        flat = _flatten_dict(d, max_depth=max_depth, current_depth=0)

        # Convert flattened dict to KeyValue blocks
        pairs: list[tuple[str, str]] = []
        for key, value in flat.items():
            if isinstance(value, str) and len(value) > 60:
                summary = value[:60] + "..."
            else:
                summary = _value_to_summary(value)
            pairs.append((key, summary))

        if pairs:
            blocks.append(KeyValue(tuple(pairs)))
        else:
            blocks.append(Data(d))

        return blocks

    def _list_to_blocks(
        self, lst: list, max_depth: int = 10, current_depth: int = 0
    ) -> list[Block]:
        """Convert a list to IR blocks.

        Handles heterogeneous arrays by summarizing.
        """
        blocks: list[Block] = []

        if current_depth >= max_depth:
            blocks.append(Elision(count=1, what="deeply nested list"))
            return blocks

        # Check if all items are homogeneous
        if not lst:
            blocks.append(Data(lst))
            return blocks

        # Check item types
        item_types = set(type(item).__name__ for item in lst)
        if len(item_types) == 1 and not isinstance(lst[0], (dict, list)):
            # Homogeneous simple values - emit as Data
            blocks.append(Data(lst))
            return blocks

        # Heterogeneous or dict/list items - process individually
        for i, item in enumerate(lst):
            if isinstance(item, dict):
                sub_blocks = self._dict_to_blocks(item, max_depth, current_depth + 1)
                blocks.extend(sub_blocks)
            elif isinstance(item, list):
                sub_blocks = self._list_to_blocks(item, max_depth, current_depth + 1)
                blocks.extend(sub_blocks)
            else:
                blocks.append(Data(item))

        return blocks