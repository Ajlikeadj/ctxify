"""ctxify: convert messy enterprise files into clean, token-optimized LLM context.

Public API::

    import ctxify
    result = ctxify.convert("report.pdf", format="md", front_matter=True)
    print(result.text, result.warnings, result.stats)
"""

from __future__ import annotations

from .errors import ConversionError
from .ir import ConversionWarning, Document, Meta
from .options import Options
from .pipeline import Result, Stats, convert

__version__ = "0.1.0"

__all__ = [
    "ConversionError",
    "ConversionWarning",
    "Document",
    "Meta",
    "Options",
    "Result",
    "Stats",
    "convert",
]
