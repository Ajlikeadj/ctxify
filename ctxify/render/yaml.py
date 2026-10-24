"""Minimal YAML emitter (no PyYAML dependency; key order is insertion order, so output is
deterministic).

Strings are emitted plain only when that is provably safe; everything else uses a JSON
double-quoted string, which is valid YAML. Tests round-trip the output through PyYAML.
"""

from __future__ import annotations

import json
import math
import re
from typing import Any

_SAFE = re.compile(r"^[A-Za-z_][A-Za-z0-9_ .,/()+-]*$")
_RESERVED = {"true", "false", "null", "yes", "no", "on", "off", "y", "n", "~"}


def _scalar(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if math.isnan(value):
            return ".nan"
        if math.isinf(value):
            return ".inf" if value > 0 else "-.inf"
        return repr(value)
    text = str(value)
    if _SAFE.match(text) and text == text.strip() and text.lower() not in _RESERVED:
        return text
    return json.dumps(text, ensure_ascii=False)


def _inline(value: Any) -> str:
    if isinstance(value, dict):
        return "{}"
    if isinstance(value, list):
        return "[]"
    return _scalar(value)


def _lines(value: Any, depth: int) -> list[str]:
    pad = "  " * depth
    if isinstance(value, dict) and value:
        out: list[str] = []
        for key, item in value.items():
            name = _scalar(str(key))
            if isinstance(item, (dict, list)) and item:
                out.append(f"{pad}{name}:")
                out.extend(_lines(item, depth + 1))
            else:
                out.append(f"{pad}{name}: {_inline(item)}")
        return out
    if isinstance(value, list) and value:
        out = []
        for item in value:
            if isinstance(item, (dict, list)) and item:
                sub = _lines(item, depth + 1)
                out.append(f"{pad}- {sub[0].lstrip()}")
                out.extend(sub[1:])
            else:
                out.append(f"{pad}- {_inline(item)}")
        return out
    return [pad + _inline(value)]


def dump(value: Any) -> str:
    """Serialize JSON-like data to YAML text ending in a newline."""
    return "\n".join(_lines(value, 0)) + "\n"
