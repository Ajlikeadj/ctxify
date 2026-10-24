from __future__ import annotations

import sys

import pytest

from ctxify import tokens


@pytest.fixture(autouse=True)
def fresh_cache():
    tokens.get_counter.cache_clear()
    yield
    tokens.get_counter.cache_clear()


def test_char_counter_rounds_up():
    c = tokens.get_counter("chars")
    assert (c.name, c.count(""), c.count("a"), c.count("abcd"), c.count("abcde")) == (
        "chars/4",
        0,
        1,
        1,
        2,
    )


def test_auto_falls_back_when_tiktoken_missing(monkeypatch):
    monkeypatch.setitem(sys.modules, "tiktoken", None)  # makes `import tiktoken` fail
    assert tokens.get_counter("auto").name == "chars/4"


def test_explicit_tiktoken_raises_when_missing(monkeypatch):
    monkeypatch.setitem(sys.modules, "tiktoken", None)
    with pytest.raises(tokens.TokenizerUnavailable):
        tokens.get_counter("tiktoken")


def test_auto_returns_a_working_counter_whatever_the_environment():
    # With network/cache tiktoken works; offline it must fall back. Either way: no crash.
    c = tokens.get_counter("auto")
    assert c.count("hello world <|endoftext|>") > 0
