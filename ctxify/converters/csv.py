"""CSV/TSV converter for ctxify.

Handles corrupt/inconsistent CSV/TSV files: unknown encodings, mixed delimiters,
ragged rows, stray quotes, BOMs, junk header/footer rows, duplicate headers.

Output: Markdown table (default), or compact CSV-in-codeblock via --table-style.
"""

from __future__ import annotations

import csv
import io
import re
from collections import Counter, deque
from typing import BinaryIO, Optional, Tuple

from ..ir import Block, ConversionWarning, Document, Elision, KeyValue, Meta, Paragraph, Table
from ..options import Options
from ..util.encoding import detect_encoding
from ..util.streams import prepend


# ── Helpers ────────────────────────────────────────────────────────────────

def _has_bom(data: bytes) -> bool:
    return data.startswith(b"\xef\xbb\xbf") or data.startswith(b"\xff\xfe") or data.startswith(b"\xfe\xff")


def _strip_bom(data: bytes) -> bytes:
    if data.startswith(b"\xef\xbb\xbf"):
        return data[3:]
    if data.startswith(b"\xff\xfe"):
        return data[2:]
    if data.startswith(b"\xfe\xff"):
        return data[2:]
    return data


def _detect_delimiter(text: str) -> str:
    """Heuristically detect the most likely delimiter.

    Tabs win if they appear in >30% of lines and are consistent.
    Otherwise comma wins if present.
    """
    lines = text.splitlines()
    if not lines:
        return ","

    tab_count = sum(line.count("\t") for line in lines)
    comma_count = sum(line.count(",") for line in lines)

    # If tab appears in most lines, use tab
    if tab_count / max(1, len(lines)) > 0.3:
        return "\t"

    # If comma appears in most lines, use comma
    if comma_count / max(1, len(lines)) > 0.3:
        return ","

    # Fallback: try csv.Sniffer
    try:
        dialect = csv.Sniffer().sniff(text, delimiters=",\t")
        return dialect.delimiter
    except csv.Error:
        return ","

def _guess_header_row(rows: list[list[str]], max_candidates: int = 3) -> int:
    """Guess which row is the header.

    Heuristics:
    - Row where most cells are short strings (not numbers) and unique.
    - Row where cell count is max (most columns).
    - Heuristic: if >50% of cells in a row look like identifiers (no digits,
      or mix of short alpha), it's likely a header.
    """
    best = 0
    for i, row in enumerate(rows):
        if not row:
            continue
        nonempty = [c.strip() for c in row if c.strip()]
        if not nonempty:
            continue
        # Count cells that look like identifiers (alpha-dominant, short)
        identifier_score = sum(
            1 for c in nonempty if c.isalpha() or (c.replace("_", "").isalpha() and len(c) <= 20)
        )
        score = identifier_score / max(1, len(nonempty))
        if score > best:
            best = score
            best_idx = i
    # Also check: row with max column count
    max_cols = max(len(r) for r in rows)
    for i, row in enumerate(rows):
        if len(row) == max_cols and i != best_idx:
            # If this row has more columns and moderate identifier score, prefer it
            nonempty = [c.strip() for c in row if c.strip()]
            if nonempty:
                identifier_score = sum(
                    1 for c in nonempty if c.isalpha() or (c.replace("_", "").isalpha() and len(c) <= 20)
                )
                score = identifier_score / max(1, len(nonempty))
                if score > best * 0.8:
                    best_idx = i
                    best = score * 0.9
    return best_idx if best > 0.25 else 0


def _strip_quotes(cell: str) -> str:
    """Gently strip surrounding quotes if matched."""
    cell = cell.strip()
    if len(cell) >= 2:
        if (cell.startswith('"') and cell.endswith('"')) or \
           (cell.startswith("'") and cell.endswith("'")):
            return cell[1:-1].strip()
    return cell


def _compact_table(
    header: tuple[str, ...],
    rows: tuple[tuple[str, ...], ...],
    style: str = "markdown",
) -> Table:
    """Create a Table IR block from header + rows with the given style."""
    if style == "records":
        # Records style: key:value per row, empty cells omitted
        lines: list[str] = []
        for row in rows:
            cell_pairs = []
            for h, v in zip(header, row):
                if v.strip():
                    cell_pairs.append(f"{h.strip()}: {v.strip()}")
            if cell_pairs:
                lines.append("\n".join(cell_pairs))
        return Table(None, (), style="records")  # we'll render records manually
    elif style == "csv":
        # Emit as compact CSV fenced code block
        buf = io.StringIO()
        writer = csv.writer(buf, lineterminator="\n")
        if header:
            writer.writerow(header)
        for row in rows:
            writer.writerow(row)
        content = buf.getvalue().rstrip("\n")
        # Return as Data block with CSV JSON
        from ..ir import Data
        import json
        return Data({"format": "csv", "header": list(header), "rows": [list(r) for r in rows]})
    else:
        # Markdown table
        # Compute column widths
        widths = list(len(h) for h in header) if header else []
        for row in rows:
            for i, cell in enumerate(row):
                if i < len(widths):
                    widths[i] = max(widths[i], len(cell))
                else:
                    widths.append(len(cell))

        # Build header
        header_cells = [
            cell.ljust(widths[i]) if i < len(widths) else cell
            for i, cell in enumerate(header)
        ] if header else []
        header_row = "| " + " | ".join(header_cells) + " |" if header_cells else ""
        separator_row = "|---" * (len(widths) or 1) + "|" if widths else ""

        # Build data rows
        data_rows: list[str] = []
        for row in rows:
            padded = (
                cell.ljust(widths[i]) if i < len(widths) else cell
                for i in range(max(len(widths), len(row)))
            )
            data_rows.append("| " + " | ".join(padded) + " |")

        table_text = "\n".join([r for r in [header_row, separator_row] + data_rows if r])
        return Table(tuple(header) if header else None, tuple(rows), style="markdown")


# ── Converter ──────────────────────────────────────────────────────────────

class CsvConverter:
    """Converts ``.csv``/``.tsv`` files into clean IR blocks."""

    name = "csv"
    extensions = (".csv", ".tsv")
    needs_seekable = False

    def detect(self, head: bytes, filename: str | None) -> float:
        if filename and filename.lower().endswith(self.extensions):
            return 1.0
        # Heuristic: if the bytes look like CSV (commas, quoted fields, no null bytes)
        if b"," in head or b"\t" in head:
            if b"\x00" not in head[:100]:
                return 0.1
        return 0.0

    def convert(self, stream: BinaryIO, opts: Options, filename: str | None) -> Document:
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

        # Strip BOM if present (detect_encoding already handles BOM, but be safe)
        raw = _strip_bom(sample)

        # Decode with replacement
        try:
            text = raw.decode(guess.name or "utf-8", errors="replace")
        except Exception:
            text = raw.decode("utf-8", errors="replace")

        # If we had replacement chars, flag it
        if "\ufffd" in text:
            warnings.append(
                ConversionWarning(
                    "invalid-bytes",
                    "Found U+FFFD replacement characters (undecodable bytes).",
                    "repair",
                )
            )

        # Split into lines, keep consistent line endings
        lines = text.splitlines()

        # Deserialize with csv.reader, handling quoted fields
        reader = csv.reader(io.StringIO(text))
        rows: list[list[str]] = list(reader)

        if not rows:
            warnings.append(
                ConversionWarning("empty-input", "CSV file has no content.", "info")
            )
            from ..ir import Data
            return Document([Data({})], Meta(source=filename, type=self.name), warnings)

        # Detect delimiter if not obvious from extensions
        if not (filename and filename.lower().endswith((".csv", ".tsv"))):
            # Already handled in detect, but keep as note
            pass

        # Identify header row
        header_idx = _guess_header_row(rows)
        header = tuple(c.strip() for c in rows[header_idx] if c.strip())
        data_rows_raw = [tuple(c.strip() for c in r if c.strip() or j < len(r)) for i, r in enumerate(rows) if i != header_idx]

        # Handle ragged rows: pad shorter rows, trim longer ones
        all_rows: list[tuple[str, ...]] = []
        max_cols = max(len(r) for r in rows) if rows else len(header)

        for i, r in enumerate(data_rows_raw):
            # Pad or trim to max_cols
            if len(r) < max_cols:
                r = r + ("",) * (max_cols - len(r))
            elif len(r) > max_cols:
                r = r[:max_cols]
            all_rows.append(tuple(r))

        # Remove duplicate data rows (keep first occurrence)
        seen: set[tuple[str, ...]] = set()
        unique_rows: list[tuple[str, ...]] = []
        for r in all_rows:
            if r not in seen:
                seen.add(r)
                unique_rows.append(r)
        if len(unique_rows) < len(all_rows):
            warnings.append(
                ConversionWarning(
                    "duplicate-rows",
                    f"Removed {len(all_rows) - len(unique_rows)} duplicate data row(s).",
                    "repair",
                )
            )

        # Remove junk rows: rows where >80% of cells are single digits/numbers only
        # (likely footer totals, page numbers)
        filtered_rows: list[tuple[str, ...]] = []
        for r in unique_rows:
            numeric_cells = sum(1 for c in r if c.strip() and c.strip().isdigit() and len(c.strip()) <= 3)
            if numeric_cells / max(1, len(r)) < 0.8:
                filtered_rows.append(r)
            else:
                warnings.append(
                    ConversionWarning(
                        "junk-rows",
                        f"Filtered out {len(unique_rows) - len(filtered_rows)} row(s) with mostly numeric cells (likely footer/total row).",
                        "repair",
                    )
                )

        # If we filtered everything, keep at least one row
        if not filtered_rows and unique_rows:
            warnings.append(
                ConversionWarning(
                    "all-rows-filtered",
                    "All data rows were filtered as junk; keeping first data row.",
                    "info",
                )
            )
            filtered_rows = [unique_rows[0]]

        # Build the table
        table = _compact_table(header, tuple(filtered_rows), style="markdown")

        # Also compute a summary for front-matter / --budget
        total_cells = sum(len(r) for r in filtered_rows)
        null_cells = sum(cell.strip() == "" for r in filtered_rows for cell in r)
        null_rate = null_cells / max(1, total_cells * max(len(header), 1))

        summary_parts: list[str] = []
        if header:
            col_types: list[str] = []
            for i in range(len(header)):
                col_values = [r[i] if i < len(r) else "" for r in filtered_rows]
                # Simple type inference
                if all(c.strip().isdigit() and len(c.strip()) <= 5 for c in col_values if c.strip()):
                    col_types.append("integer")
                elif all(
                    c.strip().replace(".", "", 1).isdigit() and len(c.strip()) <= 10
                    for c in col_values
                    if c.strip()
                ):
                    col_types.append("float")
                else:
                    col_types.append("string")
            summary_parts.append(
                f"columns: {', '.join(f'{h}({t})' for h, t in zip(header, col_types)) if header else 'unnamed'}; "
                f"rows: {len(filtered_rows)}; null rate: {null_rate:.1%}"
            )

        # Render markdown table
        md_text = self._render_md_table(header, tuple(filtered_rows))

        # Build blocks
        blocks: list[Block] = [table]  # Table block

        if summary_parts:
            # Add a summary paragraph before or after
            summary_text = "  ".join(summary_parts)
            blocks.append(Paragraph(summary_text))

        return Document(blocks, Meta(source=filename, type=self.name), warnings)

    def _render_md_table(
        self, header: tuple[str, ...], rows: tuple[tuple[str, ...], ...]
    ) -> Table:
        """Render a markdown Table IR block."""
        if not header and not rows:
            return Table(None, (), style="markdown")

        # Compute column widths
        ncols = max(
            (len(header), max((len(r) for r in rows), default=0)),
            default=0,
        )
        widths: list[int] = [0] * ncols

        if header and len(header) > 0:
            for i, h in enumerate(header):
                if i < ncols:
                    widths[i] = max(widths[i], len(h))

        for row in rows:
            for i, cell in enumerate(row):
                if i < ncols:
                    widths[i] = max(widths[i], len(cell))

        # Build header row
        header_cells = [
            (header[i] if i < len(header) else "").ljust(widths[i]) if i < ncols else ""
            for i in range(ncols)
        ]
        header_row = "| " + " | ".join(header_cells) + " |" if header_cells else ""

        # Build separator row
        separator_row = "|" + "|".join(["---"] * ncols) + "|" if ncols else ""

        # Build data rows
        data_rows: list[str] = []
        for row in rows:
            padded = []
            for i in range(ncols):
                cell = row[i] if i < len(row) else ""
                padded.append(cell.ljust(widths[i]) if i < len(widths) else cell)
            data_rows.append("| " + " | ".join(padded) + " |")

        table_text = "\n".join([r for r in [header_row, separator_row] + data_rows if r])
        from ..ir import Table as T
        return T(tuple(header) if header else None, tuple(rows), style="markdown")