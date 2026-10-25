"""Intermediate representation (IR).

Converters emit :class:`Document` objects made of typed blocks. The optimizer rewrites
blocks, and renderers print them. None of the three stages touch each other's concerns.

Streaming note: ``Document.blocks`` may be a generator, and converters may append to
``Document.warnings`` while it is consumed. Always exhaust ``blocks`` *before* reading
``warnings``. The pipeline does this for you.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Literal, TypeAlias

Severity = Literal["info", "repair", "skip"]
"""``info``: nothing lost. ``repair``: input was fixed up (guessed encoding, trimmed junk).
``skip``: content was dropped or could not be read."""

TableStyle = Literal["auto", "markdown", "csv", "records"]

# Character budgets used by the optimizer. Exposed so tests can pin them.
CELL_ABBREVIATION = 60
VALUE_ABBREVIATION = 120


@dataclass(frozen=True)
class ConversionWarning:
    """A structured note about something a converter or the optimizer repaired or skipped."""

    code: str
    message: str
    severity: Severity = "info"
    where: str | None = None


@dataclass(frozen=True)
class Heading:
    """A section heading. ``level`` is 1 (top) to 6."""

    level: int
    text: str


@dataclass(frozen=True)
class Paragraph:
    """A run of prose. Inline links are kept as ``[text](url)``."""

    text: str


@dataclass(frozen=True)
class ListBlock:
    """A bullet or numbered list. Items are strings or nested lists."""

    items: tuple[str | ListBlock, ...]
    ordered: bool = False


@dataclass(frozen=True)
class Table:
    """A rectangular table. ``style`` is a rendering hint set by the optimizer."""

    header: tuple[str, ...] | None
    rows: tuple[tuple[str, ...], ...]
    caption: str | None = None
    style: TableStyle = "markdown"


@dataclass(frozen=True)
class Code:
    """Preformatted text, such as a code or log excerpt."""

    text: str
    lang: str | None = None


@dataclass(frozen=True)
class KeyValue:
    """Ordered key/value pairs (metadata, a single record)."""

    pairs: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class Data:
    """A JSON-like tree (dict/list/str/int/float/bool/None), kept structured so it can
    be re-rendered losslessly as JSON or YAML."""

    value: object


@dataclass(frozen=True)
class Elision:
    """Marks content that was dropped, so omissions are countable and visible."""

    count: int
    what: str = "items"


Block: TypeAlias = Heading | Paragraph | ListBlock | Table | Code | KeyValue | Data | Elision


@dataclass
class Meta:
    """Facts about the source, used for front matter."""

    source: str | None = None
    type: str | None = None
    pages: int | None = None


@dataclass
class Document:
    """What a converter returns."""

    blocks: Iterable[Block]
    meta: Meta = field(default_factory=Meta)
    warnings: list[ConversionWarning] = field(default_factory=list)


__all__ = [
    "Block",
    "Code",
    "ConversionWarning",
    "Data",
    "Document",
    "Elision",
    "Heading",
    "KeyValue",
    "ListBlock",
    "Meta",
    "Paragraph",
    "Severity",
    "Table",
    "TableStyle",
]
