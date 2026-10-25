"""Property-based tests for renderers, token counting and truncation."""

from __future__ import annotations

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

import ctxify
from ctxify.ir import Table
from ctxify.pipeline import _truncate
from ctxify.render import render_body
from ctxify.render.yaml import dump
from ctxify.tokens import CharCounter, count_tokens

SETTINGS = settings(max_examples=60, deadline=None, suppress_health_check=[HealthCheck.too_slow])

SAFE_TEXT = st.text(
    alphabet=st.characters(blacklist_categories=("Cs",)),
    max_size=20,
)

JSON_LIKE = st.recursive(
    st.one_of(
        st.none(),
        st.booleans(),
        st.integers(),
        st.floats(allow_nan=False, allow_infinity=False),
        SAFE_TEXT,
    ),
    lambda children: st.one_of(
        st.lists(children, max_size=4),
        st.dictionaries(SAFE_TEXT, children, max_size=4),
    ),
    max_leaves=12,
)


@SETTINGS
@given(JSON_LIKE)
def test_yaml_round_trips_through_pyyaml(value):
    yaml = pytest.importorskip("yaml")
    assert yaml.safe_load(dump(value)) == value


@SETTINGS
@given(st.lists(SAFE_TEXT, max_size=4))
def test_markdown_table_rows_keep_a_constant_pipe_count(cells):
    header = tuple(f"c{i}" for i in range(len(cells)))
    rendered = render_body([Table(header, (tuple(cells),))], "md")
    if not cells:
        assert rendered == ""
        return
    for line in rendered.strip().splitlines():
        assert _unescaped_pipes(line) == len(cells) + 1


def _unescaped_pipes(line: str) -> int:
    count = 0
    index = 0
    while index < len(line):
        if line[index] == "\\":
            index += 2
            continue
        if line[index] == "|":
            count += 1
        index += 1
    return count


@SETTINGS
@given(SAFE_TEXT, st.integers(min_value=1, max_value=200))
def test_truncation_never_exceeds_the_budget(text, limit):
    counter = CharCounter()
    body, _ = _truncate(text, limit, counter)
    assert counter.count(body) <= limit


@SETTINGS
@given(SAFE_TEXT)
def test_token_counting_is_monotonic(extra):
    counter = CharCounter()
    base = "hello world"
    assert counter.count(base + extra) >= counter.count(base)


@SETTINGS
@given(SAFE_TEXT, st.sampled_from(["md", "json-compact", "yaml", "text"]))
def test_conversion_is_deterministic(text, fmt):
    data = text.encode("utf-8", "replace")
    first = ctxify.convert(data, name="a.txt", format=fmt, token_backend="chars")
    second = ctxify.convert(data, name="a.txt", format=fmt, token_backend="chars")
    assert first.text == second.text
    assert first.warnings == second.warnings


@SETTINGS
@given(SAFE_TEXT)
def test_stats_match_a_fresh_count(text):
    r = ctxify.convert(text.encode(), name="a.txt", token_backend="chars")
    assert r.stats.tokens == count_tokens(r.text, "chars")
