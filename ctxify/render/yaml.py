"""Minimal YAML emitter (no PyYAML dependency; key order is insertion order, so output is
deterministic).

Strings are emitted plain only when that is provably safe; everything else uses a JSON
double-quoted string, which is valid YAML. Tests round-trip the output through PyYAML.
"""

from __future__ import annotations

import math
import re
from typing import Any

_SAFE = re.compile(r"^[A-Za-z_][A-Za-z0-9_ .,/()+-]*$")
_RESERVED = {"true", "false", "null", "yes", "no", "on", "off", "y", "n", "~"}
_FORBIDDEN = re.compile(
    r"[\x00-\x08\x0b-\x1f\x7f-\x9f\u2028\u2029\ufdd0-\ufdef\ufeff\ufffe\uffff]"
)


def _quote(text: str) -> str:
    """YAML double-quoted scalar that never emits characters YAML forbids."""
    out = ['"']
    for char in text:
        code = ord(char)
        if char == '"':
            out.append('\\"')
        elif char == "\\":
            out.append("\\\\")
        elif char == "\n":
            out.append("\\n")
        elif char == "\t":
            out.append("\\t")
        elif char == "\r":
            out.append("\\r")
        elif 0xD800 <= code <= 0xDFFF:
            out.append("\\ufffd")  # lone surrogate: replace, never emit
        elif _FORBIDDEN.match(char):
            out.append(f"\\u{code:04x}")
        else:
            out.append(char)
    out.append('"')
    return "".join(out)


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
        # YAML 1.1 only recognises exponent notation when a decimal point is present
        # ("1e-05" would load as a string), so normalise "1e-05" -> "1.0e-05".
        text = repr(value)
        lowered = text.lower()
        if "e" in lowered:
            mantissa, _, exponent = lowered.partition("e")
            if "." not in mantissa:
                mantissa += ".0"
            return f"{mantissa}e{exponent}"
        return text
    text = str(value)
    if _SAFE.match(text) and text == text.strip() and text.lower() not in _RESERVED:
        return text
    return _quote(text)


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
        child_pad = "  " * (depth + 1)
        for item in value:
            if isinstance(item, (dict, list)) and item:
                sub = _lines(item, depth + 1)
                # Replace the child indent of the first line with "- " so the nested
                # mapping/sequence starts inline and its children stay correctly indented.
                first = sub[0][len(child_pad) :]
                out.append(f"{pad}- {first}")
                out.extend(sub[1:])
            else:
                out.append(f"{pad}- {_inline(item)}")
        return out
    return [pad + _inline(value)]


def dump(value: Any) -> str:
    """Serialize JSON-like data to YAML text ending in a newline."""
    return "\n".join(_lines(value, 0)) + "\n"
