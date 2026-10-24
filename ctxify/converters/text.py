"""Plain text / log converter, and the catch-all for anything that looks like text.

Cleanup: encoding detection, control-character removal, trailing whitespace removal,
runs of spaces collapsed (leading indentation kept), blank-line runs collapsed to one
paragraph break. Line structure inside a paragraph is preserved because logs need it.

Known limits: each blank-line-separated paragraph is one IR block held in memory, so a
multi-GB log with no blank lines becomes one giant block. The budget stage (step 4) will
add line-level sampling.
"""

from __future__ import annotations

import io
import re
from collections.abc import Iterator
from typing import BinaryIO

from ..ir import Block, ConversionWarning, Document, Meta, Paragraph
from ..options import Options
from ..util.encoding import detect_encoding
from ..util.streams import prepend

_SAMPLE = 64 * 1024
_CTRL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")  # keeps \t and \n
_RUNS = re.compile(r"[ \t]{2,}")
_BOMS = (b"\xef\xbb\xbf", b"\xff\xfe", b"\xfe\xff")


class TextConverter:
    """Converts ``.txt``/``.log``/``.md``-style input into paragraphs."""

    name = "text"
    extensions = (".txt", ".log", ".text", ".md", ".markdown", ".rst")
    needs_seekable = False

    def detect(self, head: bytes, filename: str | None) -> float:
        if filename and filename.lower().endswith(self.extensions):
            return 1.0
        if head.startswith(_BOMS):
            return 0.5
        return 0.0 if b"\x00" in head else 0.1

    def convert(self, stream: BinaryIO, opts: Options, filename: str | None) -> Document:
        sample = stream.read(_SAMPLE)
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
        raw = prepend(sample, stream)
        meta = Meta(source=filename, type=self.name)
        return Document(_paragraphs(raw, guess.name, warnings), meta, warnings)


def _clean(line: str) -> str:
    line = _CTRL.sub("", line).rstrip()
    body = line.lstrip()
    indent = line[: len(line) - len(body)]
    return indent + _RUNS.sub(" ", body)


def _paragraphs(raw: BinaryIO, encoding: str, warnings: list[ConversionWarning]) -> Iterator[Block]:
    text = io.TextIOWrapper(raw, encoding=encoding, errors="replace")
    flagged = False
    buf: list[str] = []
    try:
        for line in text:
            if not flagged and "\ufffd" in line:
                flagged = True
                warnings.append(
                    ConversionWarning(
                        "invalid-bytes",
                        "Found U+FFFD replacement characters (undecodable bytes, "
                        "or already present in the source).",
                        "repair",
                    )
                )
            clean = _clean(line)
            if clean.strip():
                buf.append(clean)
            elif buf:
                yield Paragraph("\n".join(buf))
                buf = []
        if buf:
            yield Paragraph("\n".join(buf))
    finally:
        text.detach()  # do not close the caller's stream
