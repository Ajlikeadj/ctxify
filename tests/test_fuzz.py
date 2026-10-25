"""Fuzz-style tests: arbitrary bytes and text must never escape as an exception."""

from __future__ import annotations

import random

import pytest

import ctxify
from ctxify.ir import ConversionWarning

FORMATS = ["md", "json-compact", "yaml", "text"]
NAMES = ["a.txt", "a.csv", "a.tsv", "a.json", "a.jsonl", "a.docx", "a.bin", None]


def convert(data: bytes, *, name: str | None, format: str):  # type: ignore[no-untyped-def]
    return ctxify.convert(data, name=name, format=format, token_backend="chars")


@pytest.mark.parametrize("fmt", FORMATS)
def test_random_bytes_never_raise(fmt):
    rng = random.Random(0xC7F1)
    for _ in range(40):
        data = bytes(rng.getrandbits(8) for _ in range(rng.randrange(0, 1500)))
        r = convert(data, name=rng.choice(NAMES), format=fmt)
        assert isinstance(r.text, str)
        assert r.stats.tokens >= 0
        assert all(isinstance(w, ConversionWarning) for w in r.warnings)


@pytest.mark.parametrize("fmt", FORMATS)
def test_random_unicode_text_never_raises(fmt):
    rng = random.Random(42)
    alphabet = "abc ABC 0123\n\t,;|{}\"'[]:#-_\u00e9\u00fc\u65e5\u672c\u0000\u0007\ufffd"
    for _ in range(40):
        text = "".join(rng.choice(alphabet) for _ in range(rng.randrange(0, 600)))
        r = convert(text.encode("utf-8", "replace"), name=rng.choice(NAMES), format=fmt)
        assert isinstance(r.text, str)


def test_truncated_utf16_does_not_crash():
    data = b"\xff\xfe" + "hello".encode("utf-16-le")[:5]
    r = convert(data, name="a.txt", format="md")
    assert isinstance(r.text, str)


def test_random_byte_input_is_deterministic():
    rng = random.Random(7)
    data = bytes(rng.getrandbits(8) for _ in range(500))
    outputs = {convert(data, name="a.csv", format="md").text for _ in range(3)}
    assert len(outputs) == 1


def test_control_characters_never_reach_output():
    data = b"visible\x01\x02text\x07\x1b done\n"
    r = convert(data, name="a.txt", format="md")
    assert r.stats.converter == "text"
    assert all(ord(ch) >= 32 or ch in "\n\t" for ch in r.text)
    assert "visibletext" in r.text


def test_lone_surrogate_from_json_escape_is_replaced():
    r = ctxify.convert(b'"\\ud800"', name="a.json", token_backend="chars")
    assert "\ud800" not in r.text
    assert "\ufffd" in r.text
    assert "invalid-unicode" in [w.code for w in r.warnings]


def test_every_output_format_handles_lone_surrogates():
    for fmt in FORMATS:
        r = ctxify.convert(b'{"x": "\\udfff"}', name="a.json", format=fmt, token_backend="chars")
        r.text.encode("utf-8")  # must not raise


def test_malformed_json_variants_are_contained():
    variants = [
        b"{",
        b"[[[[",
        b'{"a":',
        b'{"a": 1} trailing',
        b"\x00\x01\x02",
        b'{"a": [1, 2,]}',
        b"{" * 10_000,
    ]
    for data in variants:
        r = convert(data, name="a.json", format="md")
        assert isinstance(r.text, str)


def test_malformed_csv_variants_are_contained():
    variants = [
        b'"',
        b'"unterminated',
        b"a,b\n" + b'"' * 500,
        b"\x00,\x01\n2,3\n",
        b"a,b\n" + b"1," * 5000 + b"\n",
    ]
    for data in variants:
        r = convert(data, name="a.csv", format="md")
        assert isinstance(r.text, str)
