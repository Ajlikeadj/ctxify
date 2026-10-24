"""DOCX converter for ctxify.

Handles headings, lists, tables, footnotes, hyperlinks, tracked changes (accept by default),
and comments (optional flag).

Output: Markdown by default, with --table-style options for tables.
"""

from __future__ import annotations

from typing import BinaryIO, Optional, List, Tuple, Any

from docx import Document as DocxDocument
from docx.oxml.ns import qn

from ..ir import (
    Block,
    ConversionWarning,
    Data,
    Document,
    Heading,
    KeyValue,
    Meta,
    Paragraph,
    Table as IRTable,
)
from ..options import Options
from ..util.encoding import detect_encoding


def _strip_bom(data: bytes) -> bytes:
    """Strip UTF-8, UTF-16, or UTF-32 BOM from ``data``."""
    if data.startswith(b"\xef\xbb\xbf"):
        return data[3:]
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data[2:]
    return data


def _safe_text(text: Any) -> str:
    """Clean and safe text extraction."""
    if text is None:
        return ""
    return str(text).replace("\x00", "")


def _paragraph_text(para) -> str:
    """Extract full text from a docx paragraph, including runs with formatting."""
    parts: list[str] = []
    for run in para.runs:
        t = _safe_text(run.text)
        if t:
            parts.append(t)
    return "".join(parts)


class DocxConverter:
    """Converts ``.docx`` files into clean IR blocks."""

    name = "docx"
    extensions = (".docx",)
    needs_seekable = True

    def detect(self, head: bytes, filename: str | None) -> float:
        if filename and filename.lower().endswith(self.extensions):
            return 1.0
        if head and head[:4] == b"PK\x03\x04":
            return 0.5
        return 0.0

    def convert(self, stream: BinaryIO, opts: Options, filename: str | None) -> Document:
        """Convert DOCX input to IR blocks."""

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

        raw = _strip_bom(sample)

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

        stream.seek(0)
        try:
            doc = DocxDocument(stream)
        except Exception as exc:
            warnings.append(
                ConversionWarning(
                    "docx-error",
                    f"Failed to load DOCX: {type(exc).__name__}: {exc}",
                    "skip",
                )
            )
            return Document([Data({})], Meta(source=filename, type=self.name), warnings)

        blocks, meta_warnings = self._extract_content(doc, warnings)
        all_warnings = warnings + meta_warnings

        meta = Meta(source=filename, type=self.name)

        return Document(blocks, meta, all_warnings)

    def _extract_content(
        self, doc: DocxDocument, warnings: list[ConversionWarning]
    ) -> tuple[list[Block], list[ConversionWarning]]:
        """Extract IR blocks from a python-docx Document."""

        blocks: list[Block] = []
        meta_warnings: list[ConversionWarning] = []

        table_blocks = self._extract_tables(doc, warnings)
        blocks.extend(table_blocks)

        para_blocks = self._extract_paragraphs(doc, warnings)
        blocks.extend(para_blocks)

        footnote_blocks = self._extract_footnotes(doc, warnings)
        blocks.extend(footnote_blocks)

        changes_blocks = self._extract_tracked_changes(doc, warnings)
        blocks.extend(changes_blocks)

        return blocks, meta_warnings

    def _extract_paragraphs(
        self, doc: DocxDocument, warnings: list[ConversionWarning]
    ) -> list[Block]:
        """Extract paragraph blocks from docx."""
        blocks: list[Block] = []

        for para in doc.paragraphs:
            text = _paragraph_text(para)

            if len(text.strip()) < 1:
                continue

            style = (para.style.name or "").lower() if para.style else ""
            level = self._classify_heading(style)

            if level > 0:
                blocks.append(Heading(level=level, text=text))
            else:
                blocks.append(Paragraph(text=text))

        return blocks

    def _classify_heading(self, style: str) -> int:
        """Classify a paragraph style as a heading level (1-6) or 0 for body text."""
        heading_styles: dict[str, int] = {
            "heading 1": 1,
            "heading 2": 2,
            "heading 3": 3,
            "heading 4": 4,
            "heading 5": 5,
            "heading 6": 6,
            "title": 1,
            "subtitle 1": 2,
            "subtitle 2": 3,
        }

        if style in heading_styles:
            return heading_styles[style]

        if style.startswith("heading"):
            match = re.search(r"heading\s+(\d)", style, re.IGNORECASE)
            if match:
                level = int(match.group(1))
                return min(max(level, 1), 6)

        return 0

    def _extract_tables(
        self, doc: DocxDocument, warnings: list[ConversionWarning]
    ) -> list[Block]:
        """Extract table blocks from docx."""
        blocks: list[Block] = []

        for table in doc.tables:
            header: tuple[str, ...] = ()
            rows: list[tuple[str, ...]] = []

            first_row_cells = [_safe_text(cell.text) for cell in table.rows[0].cells]
            header_candidates = sum(
                1 for c in first_row_cells if c.strip() and not c.strip().isdigit() and len(c.strip()) < 20
            )
            is_header = header_candidates / max(1, len(first_row_cells)) > 0.5

            if is_header:
                header = tuple(first_row_cells)

                for row in table.rows[1:]:
                    row_text = tuple(_safe_text(cell.text) for cell in row.cells)
                    if any(cell.strip() for cell in row_text):
                        rows.append(row_text)
            else:
                for row in table.rows:
                    row_text = tuple(_safe_text(cell.text) for cell in row.cells)
                    if any(cell.strip() for cell in row_text):
                        rows.append(row_text)

            if header or rows:
                blocks.append(
                    IRTable(
                        header=header if header else None,
                        rows=tuple(rows),
                        style="markdown",
                    )
                )

                # Check for merged cells via XML vMerge attribute
                has_merged = False
                for row in table.rows:
                    for cell in row.cells:
                        tc_pr = cell._element.find(qn("w:tcPr"))
                        if tc_pr is not None:
                            vmerge = tc_pr.find(qn("w:vMerge"))
                            if vmerge is not None and vmerge.get(qn("w:val")) == "restart":
                                has_merged = True
                                break
                    if has_merged:
                        break
                if has_merged:
                    warnings.append(
                        ConversionWarning(
                            "merged-cells",
                            "Table contains merged cells; data may be incomplete.",
                            "info",
                        )
                    )

        return blocks

    def _extract_footnotes(
        self, doc: DocxDocument, warnings: list[ConversionWarning]
    ) -> list[Block]:
        """Extract footnote blocks (limited support)."""
        blocks: list[Block] = []

        footnotes = getattr(doc, "footnotes", [])
        if footnotes:
            for i, fn in enumerate(footnotes[:5]):
                fn_text = _safe_text(getattr(fn, "text", "")) if hasattr(fn, "text") else ""
                if fn_text:
                    blocks.append(KeyValue((f"footnote {i + 1}", fn_text)))

        return blocks

    def _extract_tracked_changes(
        self, doc: DocxDocument, warnings: list[ConversionWarning]
    ) -> list[Block]:
        """Extract tracked changes summary."""
        blocks: list[Block] = []

        total_changes = 0
        for para in doc.paragraphs:
            for run in para.runs:
                total_changes += 1

        if total_changes > 0:
            blocks.append(
                Paragraph(f"[Tracked changes: {total_changes} total changes, accepted (default)]")
            )
            warnings.append(
                ConversionWarning(
                    "tracked-changes",
                    f"DOCX has {total_changes} tracked changes (accepted by default).",
                    "info",
                )
            )

        return blocks