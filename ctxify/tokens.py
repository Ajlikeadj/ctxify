"""Token counting: tiktoken when usable, a fast character heuristic otherwise.

The backend name is recorded in ``Result.stats`` and front matter, because the two
backends give different numbers and cached results should say which produced them.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Protocol


class TokenizerUnavailable(RuntimeError):
    """The explicitly requested token backend could not be used."""


class TokenCounter(Protocol):
    """Anything that can count tokens in a string."""

    name: str

    def count(self, text: str) -> int:
        """Return the (estimated) token count of ``text``."""
        ...


class CharCounter:
    """``ceil(len(text) / 4)``: ~4 chars/token is a decent English average.

    Failure mode: it undercounts CJK text and dense code or JSON (more tokens per char).
    """

    name = "chars/4"

    def count(self, text: str) -> int:
        return (len(text) + 3) // 4


class _TiktokenCounter:
    def __init__(self, encoding: str) -> None:
        import tiktoken  # lazy: heavy import

        self._enc = tiktoken.get_encoding(encoding)  # may fetch a file on first use
        self.name = f"tiktoken:{encoding}"

    def count(self, text: str) -> int:
        # disallowed_special=() so text like "<|endoftext|>" is counted, not rejected.
        return len(self._enc.encode(text, disallowed_special=()))


@lru_cache(maxsize=8)
def get_counter(backend: str = "auto", encoding: str = "o200k_base") -> TokenCounter:
    """Return a token counter.

    Args:
        backend: ``auto`` falls back to :class:`CharCounter` if tiktoken is missing or its
            encoding cannot be loaded (e.g. offline). ``tiktoken`` raises
            :class:`TokenizerUnavailable` instead. ``chars`` always uses the heuristic.
        encoding: tiktoken encoding name.
    """
    if backend == "chars":
        return CharCounter()
    try:
        return _TiktokenCounter(encoding)
    except Exception as exc:  # ImportError, network errors, bad encoding name
        if backend == "tiktoken":
            raise TokenizerUnavailable(f"tiktoken backend unavailable: {exc}") from exc
        return CharCounter()


def count_tokens(text: str, backend: str = "auto") -> int:
    """Convenience wrapper: count tokens in ``text`` with the chosen backend."""
    return get_counter(backend).count(text)
