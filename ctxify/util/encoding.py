"""Text-encoding detection.

Order: BOM, strict UTF-8, ``charset-normalizer`` (if installed), then cp1252.

Failure modes: short samples are inherently ambiguous (cp1252, latin-1 and cp1250 agree
on most accented letters), and the cp1252 fallback can mis-decode CJK or Cyrillic files.
Callers should surface ``method != "utf-8"`` results as repair warnings.
"""

from __future__ import annotations

import codecs
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class EncodingGuess:
    """A codec name plus how it was chosen: ``bom``, ``utf-8``, ``detected`` or ``fallback``."""

    name: str
    method: Literal["bom", "utf-8", "detected", "fallback"]


def detect_encoding(sample: bytes) -> EncodingGuess:
    """Guess the encoding of ``sample`` (the first chunk of a file)."""
    if sample.startswith(codecs.BOM_UTF8):
        return EncodingGuess("utf-8-sig", "bom")
    # UTF-32 LE starts with the UTF-16 LE BOM, so it must be checked first.
    if sample.startswith((codecs.BOM_UTF32_LE, codecs.BOM_UTF32_BE)):
        return EncodingGuess("utf-32", "bom")
    if sample.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return EncodingGuess("utf-16", "bom")
    try:
        # final=False tolerates a multibyte character cut off at the end of the sample.
        codecs.getincrementaldecoder("utf-8")().decode(sample, final=False)
        return EncodingGuess("utf-8", "utf-8")
    except UnicodeDecodeError:
        pass
    try:
        from charset_normalizer import from_bytes
    except ImportError:
        return EncodingGuess("cp1252", "fallback")
    best = from_bytes(sample).best()
    if best is not None and best.encoding:
        try:
            codecs.lookup(best.encoding)
            return EncodingGuess(best.encoding, "detected")
        except LookupError:
            pass
    return EncodingGuess("cp1252", "fallback")


def _strip_bom(data: bytes) -> bytes:
    """Strip a UTF-8, UTF-16 or UTF-32 BOM from ``data``."""
    if data.startswith(codecs.BOM_UTF8):
        return data[3:]
    # UTF-32 LE starts with the UTF-16 LE BOM, so it must be checked first.
    if data.startswith((codecs.BOM_UTF32_LE, codecs.BOM_UTF32_BE)):
        return data[4:]
    if data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return data[2:]
    return data
