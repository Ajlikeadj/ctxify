"""CSV/TSV converter for ctxify.

Handles corrupt/inconsistent CSV/TSV files: unknown encodings, mixed delimiters,
ragged rows, stray quotes, BOMs and duplicate headers.

Fidelity rules:

* The whole stream is read (never just the first block of bytes).
* Rows and cells are never dropped or deduplicated: a data conversion tool must not
  silently change the data. Structural problems become warnings instead.
* ``--budget summary`` emits column statistics and a sample; ``full`` keeps every row.
"""

from __future__ import annotations

import codecs
import csv
import io
import re
from collections import Counter
from collections.abc import Sequence
from itertools import chain
from typing import BinaryIO

from ..ir import Block, ConversionWarning, Document, Elision, Meta, Paragraph, Table
from ..options import Options
from ..util.encoding import detect_encoding
from ..util.streams import prepend

_SAMPLE = 64 * 1024
_SNIFF_LINES = 30
_SAMPLE_ROWS = 20
_DELIMITERS = (",", ";", "\t", "|")
_NUMBER = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2})?)?$")
_BOOLS = {"true", "false", "yes", "no"}


def _is_number(cell: str) -> bool:
    return bool(_NUMBER.match(cell.strip()))


def _looks_like_header(row: Sequence[str]) -> bool:
    """A header row has non-empty, unique, non-numeric cells."""
    cells = [c.strip() for c in row if c.strip()]
    return bool(cells) and len(set(cells)) == len(cells) and not any(_is_number(c) for c in cells)


def _sniff_delimiter(lines: Sequence[str], filename: str | None) -> str:
    """Pick the delimiter that produces the most consistent column count."""
    if filename and filename.lower().endswith(".tsv"):
        return "\t"
    best = (0.0, 0, ",")
    for delim in _DELIMITERS:
        try:
            rows = [r for r in csv.reader(lines, delimiter=delim) if r]
        except csv.Error:
            continue
        counts = [len(r) for r in rows]
        if not counts:
            continue
        columns, freq = Counter(counts).most_common(1)[0]
        if columns < 2:
            continue
        candidate = (freq / len(counts), columns, delim)
        if candidate[:2] > best[:2]:
            best = candidate
    return best[2]


def _column_type(values: list[str]) -> str:
    present = [v.strip() for v in values if v.strip()]
    if not present:
        return "empty"
    if all(_is_number(v) and "." not in v and "e" not in v.lower() for v in present):
        return "integer"
    if all(_is_number(v) for v in present):
        return "float"
    if all(v.lower() in _BOOLS for v in present):
        return "boolean"
    if all(_DATE.match(v) for v in present):
        return "date"
    return "string"


def _summary(header: tuple[str, ...] | None, rows: Sequence[tuple[str, ...]], width: int) -> str:
    names = header or tuple(f"col{i + 1}" for i in range(width))
    nulls = sum(1 for row in rows for cell in row if not cell.strip())
    total = len(rows) * width
    rate = nulls / total if total else 0.0
    types = [_column_type([row[i] for row in rows]) for i in range(width)]
    columns = ", ".join(f"{name}({kind})" for name, kind in zip(names, types, strict=False))
    return f"columns: {columns}; rows: {len(rows)}; null rate: {rate:.1%}"


class CsvConverter:
    """Converts ``.csv``/``.tsv`` files into clean IR blocks."""

    name = "csv"
    extensions = (".csv", ".tsv")
    needs_seekable = False

    def detect(self, head: bytes, filename: str | None) -> float:
        if filename and filename.lower().endswith(self.extensions):
            return 1.0
        # Weak sniff only: without an extension, text is a safer default.
        if (b"," in head or b"\t" in head or b";" in head) and b"\x00" not in head[:100]:
            return 0.1
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
        # final=False so a multibyte character split at the sample boundary is not
        # mistaken for an invalid byte.
        try:
            probe = codecs.getincrementaldecoder(guess.name)().decode(sample, final=False)
        except (LookupError, UnicodeDecodeError):
            probe = sample.decode(guess.name, errors="replace")
        if "\ufffd" in probe:
            warnings.append(
                ConversionWarning(
                    "invalid-bytes",
                    "Found U+FFFD replacement characters (undecodable bytes).",
                    "repair",
                )
            )

        raw = prepend(sample, stream)
        text = io.TextIOWrapper(raw, encoding=guess.name, errors="replace", newline="")
        rows: list[list[str]] = []
        try:
            head_lines: list[str] = []
            for _ in range(_SNIFF_LINES):
                try:
                    head_lines.append(next(text))
                except StopIteration:
                    break
            delim = _sniff_delimiter(head_lines, filename)
            reader = csv.reader(chain(head_lines, text), delimiter=delim, skipinitialspace=True)
            try:
                for row in reader:
                    if any(cell.strip() for cell in row):
                        rows.append(row)
            except csv.Error as exc:
                warnings.append(
                    ConversionWarning(
                        "csv-error",
                        f"Stopped at malformed row {len(rows) + 1}: {exc}",
                        "skip",
                    )
                )
        finally:
            text.detach()

        if not rows:
            warnings.append(ConversionWarning("empty-input", "CSV file has no content.", "info"))
            return Document([], Meta(source=filename, type=self.name), warnings)

        header: tuple[str, ...] | None = None
        data = rows
        if _looks_like_header(rows[0]):
            header = tuple(cell.strip() for cell in rows[0])
            data = rows[1:]

        width = max([len(header or ())] + [len(row) for row in data], default=0)
        if width == 0:
            return Document([], Meta(source=filename, type=self.name), warnings)

        if header is not None:
            if len(header) < width:
                missing = width - len(header)
                header = header + tuple(f"col{i + 1}" for i in range(len(header), width))
                warnings.append(
                    ConversionWarning(
                        "header-padded",
                        f"Header had {len(header) - missing} column(s) but data has {width}; "
                        "missing names were generated.",
                        "repair",
                    )
                )
            header = tuple(name if name.strip() else f"col{i + 1}" for i, name in enumerate(header))

        ragged = sum(1 for row in data if len(row) != width)
        if ragged:
            warnings.append(
                ConversionWarning(
                    "ragged-rows",
                    f"{ragged} row(s) had a different column count; padded or extended "
                    f"to {width} columns.",
                    "repair",
                )
            )
        columns = tuple(
            tuple(row) + ("",) * (width - len(row)) for row in data
        )

        if opts.budget == "summary":
            sample_rows = columns[:_SAMPLE_ROWS]
            blocks: list[Block] = [
                Paragraph(_summary(header, columns, width)),
                Table(header, sample_rows, style=opts.table_style),
            ]
            if len(columns) > len(sample_rows):
                blocks.append(Elision(len(columns) - len(sample_rows), "table rows"))
        else:
            blocks = [Table(header, columns, style=opts.table_style)]

        return Document(blocks, Meta(source=filename, type=self.name), warnings)
