"""JSON/JSONL converter for ctxify.

Reads the whole document rather than a truncated sample, reports duplicate keys and
non-finite numbers instead of hiding them, and shapes the tree so that the common cases
are token-efficient:

* a flat object becomes one ``KeyValue`` block;
* an array of objects becomes a ``Table`` (union of keys, first-seen order);
* an array of strings becomes a ``ListBlock``;
* anything else stays a ``Data`` block, which is lossless for JSON and YAML output.

``--budget summary`` samples oversized arrays instead of emitting every element.
"""

from __future__ import annotations

import json
import math
from typing import Any, BinaryIO

from ..ir import Block, ConversionWarning, Data, Document, Elision, KeyValue, ListBlock, Meta, Table
from ..options import Options
from ..util.encoding import detect_encoding
from ..util.streams import prepend

_SAMPLE = 64 * 1024
_SUMMARY_ROWS = 20
_MAX_TABLE_KEYS = 20
_MAX_NESTING = 200
_UNSET = object()


def _compact(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def _cell(value: Any) -> str:
    """Render one JSON value as a table cell / key-value string."""
    if value is None:
        return ""
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, str):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            return str(value)
        return json.dumps(value)
    if isinstance(value, int):
        return str(value)
    return _compact(value)


def _sanitize_text(text: str) -> str:
    """Replace lone surrogates (from ``\\ud800``-style escapes) with U+FFFD."""
    return text.encode("utf-8", "surrogatepass").decode("utf-8", "replace")


def _finite(value: Any) -> Any:
    """Replace NaN/Infinity and lone surrogates so every output encoding stays valid."""
    if isinstance(value, float):
        return str(value) if not math.isfinite(value) else value
    if isinstance(value, str):
        return _sanitize_text(value)
    if isinstance(value, dict):
        return {_sanitize_text(str(k)): _finite(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_finite(v) for v in value]
    return value


def _has_surrogates(value: Any) -> bool:
    stack: list[Any] = [value]
    while stack:
        node = stack.pop()
        if isinstance(node, str):
            if any("\ud800" <= char <= "\udfff" for char in node):
                return True
        elif isinstance(node, dict):
            stack.extend(node.keys())
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)
    return False


def _depth(value: Any) -> int:
    """Iterative nesting depth, so pathological input cannot exhaust the stack."""
    deepest = 0
    stack: list[tuple[Any, int]] = [(value, 1)]
    while stack:
        node, level = stack.pop()
        deepest = max(deepest, level)
        if isinstance(node, dict):
            stack.extend((child, level + 1) for child in node.values())
        elif isinstance(node, list):
            stack.extend((child, level + 1) for child in node)
    return deepest


def _union_keys(records: list[dict[str, Any]]) -> tuple[str, ...]:
    seen: dict[str, None] = {}
    for record in records:
        for key in record:
            seen.setdefault(key, None)
    return tuple(seen)


class JsonConverter:
    """Converts ``.json``/``.jsonl`` files into clean IR blocks."""

    name = "json"
    extensions = (".json", ".jsonl")
    needs_seekable = False

    def detect(self, head: bytes, filename: str | None) -> float:
        if filename and filename.lower().endswith(self.extensions):
            return 1.0
        stripped = head.lstrip(b"\xef\xbb\xbf \t\r\n")
        if stripped[:1] in (b"{", b"[") and b"\x00" not in head[:100]:
            return 0.5
        return 0.0

    def convert(self, stream: BinaryIO, opts: Options, filename: str | None) -> Document:
        warnings: list[ConversionWarning] = []
        sample = stream.read(_SAMPLE)
        guess = detect_encoding(sample)
        if guess.method in ("detected", "fallback"):
            warnings.append(
                ConversionWarning(
                    "encoding-guessed",
                    f"Input is not valid UTF-8; decoded as {guess.name} ({guess.method}).",
                    "repair",
                )
            )

        raw = prepend(sample, stream)
        try:
            text = raw.read().decode(guess.name, errors="replace")
        finally:
            raw.close()

        if "\ufffd" in text:
            warnings.append(
                ConversionWarning(
                    "invalid-bytes",
                    "Found U+FFFD replacement characters (undecodable bytes).",
                    "repair",
                )
            )

        duplicates: list[str] = []
        constants: list[str] = []
        value = _parse_json(text, duplicates, constants)

        if value is _UNSET:
            records, bad_lines = _parse_jsonl(text)
            if records is None:
                warnings.append(
                    ConversionWarning(
                        "invalid-json",
                        "Input is neither valid JSON nor valid JSON Lines; nothing was emitted.",
                        "skip",
                    )
                )
                return Document([], Meta(source=filename, type=self.name), warnings)
            if bad_lines:
                warnings.append(
                    ConversionWarning(
                        "jsonl-invalid-lines",
                        f"Skipped {bad_lines} line(s) that were not valid JSON.",
                        "repair",
                    )
                )
            value = records

        if _has_surrogates(value):
            warnings.append(
                ConversionWarning(
                    "invalid-unicode",
                    "Lone UTF-16 surrogates were replaced with U+FFFD.",
                    "repair",
                )
            )

        if _depth(value) > _MAX_NESTING:
            warnings.append(
                ConversionWarning(
                    "json-too-deep",
                    f"Nesting depth exceeds {_MAX_NESTING}; "
                    "the document was skipped rather than flattened.",
                    "skip",
                )
            )
            return Document([], Meta(source=filename, type=self.name), warnings)

        if duplicates:
            warnings.append(
                ConversionWarning(
                    "duplicate-keys",
                    "Duplicate object keys kept the last value: "
                    + ", ".join(sorted(set(duplicates))),
                    "repair",
                )
            )
        if constants:
            warnings.append(
                ConversionWarning(
                    "non-finite-number",
                    "Non-standard JSON constants were preserved as strings in output: "
                    + ", ".join(sorted(set(constants))),
                    "repair",
                )
            )

        blocks = self._blocks(value, opts)
        return Document(blocks, Meta(source=filename, type=self.name), warnings)

    def _blocks(self, value: Any, opts: Options) -> list[Block]:
        if isinstance(value, dict) and value and all(
            not isinstance(v, (dict, list)) for v in value.values()
        ):
            return [
                KeyValue(tuple((str(k), _cell(_finite(v))) for k, v in value.items()))
            ]

        if isinstance(value, list) and value and all(isinstance(v, dict) for v in value):
            records: list[dict[str, Any]] = value
            keys = _union_keys(records)
            if keys and len(keys) <= _MAX_TABLE_KEYS:
                rows = tuple(
                    tuple(_cell(_finite(record.get(key))) for key in keys) for record in records
                )
                if opts.budget == "summary" and len(rows) > _SUMMARY_ROWS:
                    return [
                        Table(tuple(keys), rows[:_SUMMARY_ROWS], style=opts.table_style),
                        Elision(len(rows) - _SUMMARY_ROWS, "table rows"),
                    ]
                return [Table(tuple(keys), rows, style=opts.table_style)]

        if isinstance(value, list) and value and all(isinstance(v, str) for v in value):
            items: tuple[str, ...] = tuple(_finite(v) for v in value)
            if opts.budget == "summary" and len(items) > 100:
                return [ListBlock(items[:50]), Elision(len(items) - 50, "list items")]
            return [ListBlock(items)]

        if isinstance(value, list) and opts.budget == "summary" and len(value) > 50:
            return [Data(_finite(value[:50])), Elision(len(value) - 50, "list items")]

        return [Data(_finite(value))]


def _parse_json(text: str, duplicates: list[str], constants: list[str]) -> Any:
    """Parse one JSON document; ``_UNSET`` means "not a single JSON document"."""

    def pairs_hook(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                duplicates.append(key)
            result[key] = value
        return result

    def constant_hook(name: str) -> float:
        constants.append(name)
        return {"NaN": float("nan"), "Infinity": float("inf"), "-Infinity": float("-inf")}[name]

    try:
        return json.loads(text, object_pairs_hook=pairs_hook, parse_constant=constant_hook)
    except (json.JSONDecodeError, ValueError, RecursionError):
        return _UNSET


def _parse_jsonl(text: str) -> tuple[list[Any] | None, int]:
    """Parse JSON Lines; ``None`` if the input is clearly not JSONL."""
    records: list[Any] = []
    bad = 0
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            records.append(json.loads(line))
        except (json.JSONDecodeError, ValueError, RecursionError):
            bad += 1
    if not records:  # nothing parsed at all
        return None, bad
    if len(records) == 1 and not bad and isinstance(records[0], (dict, list)):
        # A single JSON object on one line is a JSON document, not JSONL.
        return None, 0
    return records, bad
