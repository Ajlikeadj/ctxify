"""Token budget: the hard limit, the progressive stages and well-formed output."""

from __future__ import annotations

import json

import pytest

import ctxify
from ctxify.tokens import count_tokens

FORMATS = ["md", "json-compact", "yaml", "text"]


def convert(data: bytes, *, name: str = "a.txt", **options):  # type: ignore[no-untyped-def]
    options.setdefault("token_backend", "chars")
    return ctxify.convert(data, name=name, options=ctxify.Options(**options))


CASES = [
    (b"word " * 500, "a.txt"),
    (b"one\n\ntwo\n\nthree\n\nfour\n\nfive\n", "a.txt"),
    (b"x" * 5000, "a.txt"),
    (b"# H\n\n" + b"para\n\n" * 50, "a.md"),
    (b"name,qty\n" + b"widget,3\n" * 200, "a.csv"),
    (json.dumps([{"a": i, "b": "x" * 20} for i in range(100)]).encode(), "a.json"),
    (b'{"a":1}\n{"a":2}\n' * 100, "a.jsonl"),
    ((b"# Title\n\n" + b"| a | b |\n|---|---|\n" + b"| 1 | 2 |\n" * 100), "a.md"),
]


@pytest.mark.parametrize("fmt", FORMATS)
@pytest.mark.parametrize("limit", [1, 5, 20, 100])
@pytest.mark.parametrize("data,name", CASES)
def test_budget_is_never_exceeded(data, name, limit, fmt):
    r = convert(data, name=name, format=fmt, max_tokens=limit)
    assert r.stats.tokens <= limit
    assert count_tokens(r.text, "chars") == r.stats.tokens


def test_no_budget_means_no_reduction():
    r = convert(b"word " * 500, max_tokens=None)
    assert r.stats.dropped == ()
    assert r.text.startswith("word word")


def test_budget_that_already_fits_changes_nothing():
    r = convert(b"hello world\n")
    unbudgeted = convert(b"hello world\n", max_tokens=1000)
    assert r.text == unbudgeted.text
    assert unbudgeted.stats.dropped == ()


def test_dedupe_stage_is_reported():
    r = convert(b"same paragraph\n\n" * 40, max_tokens=50)
    assert any("deduplicated" in note for note in r.stats.dropped)


def test_abbreviation_stage_is_reported():
    long_cell = b"y" * 500
    data = b"a,b\n" + (long_cell + b"," + long_cell + b"\n") * 5
    r = convert(data, name="a.csv", max_tokens=60)
    assert any("abbreviated" in note for note in r.stats.dropped)


def test_table_sampling_stage_is_reported():
    data = b"a,b\n" + b"".join(f"row-{i},{i}\n".encode() for i in range(200))
    r = convert(data, name="a.csv", max_tokens=120)
    assert any("sampled tables" in note for note in r.stats.dropped)
    assert "[... " in r.text


def test_header_only_stage_is_reported():
    data = b"a,b\n" + b"".join(f"row-{i},{i}\n".encode() for i in range(200))
    r = convert(data, name="a.csv", max_tokens=12)
    assert any(
        ("kept table headers only" in note) or ("sampled tables" in note)
        for note in r.stats.dropped
    )


def test_trailing_blocks_are_dropped_not_cut_mid_paragraph():
    data = b"".join(f"paragraph-{i:02d}\n\n".encode() for i in range(20))
    r = convert(data, name="a.txt", max_tokens=20)
    assert r.stats.tokens <= 20
    assert "paragraph-00" in r.text
    assert r.text.rstrip().endswith(tuple(f"paragraph-{i:02d}" for i in range(20)))


def test_truncated_code_fence_is_closed():
    data = b"a,b\n" + b"".join(f"row-{i},{i}\n".encode() for i in range(200))
    for limit in range(1, 20):
        r = convert(data, name="a.csv", table_style="csv", max_tokens=limit)
        assert r.text.count("```") % 2 == 0, (limit, r.text)


def test_front_matter_is_excluded_from_the_budget():
    r = convert(b"word " * 200, name="a.txt", max_tokens=10, front_matter=True)
    assert r.stats.tokens <= 10
    body = r.text.split("---\n\n", 1)[1]
    assert count_tokens(body, "chars") == r.stats.tokens
    assert "tokens: " in r.text


def test_front_matter_records_the_dropped_stages():
    r = convert(b"word " * 500, name="a.txt", max_tokens=10, front_matter=True)
    assert "dropped:" in r.text
    assert "truncated output" in r.text


def test_dropped_stages_are_unique():
    r = convert(b"name,qty\n" + b"widget,3\n" * 500, name="a.csv", max_tokens=40)
    assert len(r.stats.dropped) == len(set(r.stats.dropped))


def test_zero_budget_is_rejected():
    with pytest.raises(ValueError, match="max_tokens"):
        ctxify.Options(max_tokens=0)
    with pytest.raises(ValueError, match="max_tokens"):
        ctxify.Options(max_tokens=-5)


def test_budget_is_reproducible():
    data = b"a,b\n" + b"".join(f"row-{i},{i}\n".encode() for i in range(100))
    outputs = {convert(data, name="a.csv", max_tokens=50).text for _ in range(3)}
    assert len(outputs) == 1
