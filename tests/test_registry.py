from __future__ import annotations

import pytest

from ctxify import registry
from ctxify.ir import Document


class Fake:
    extensions: tuple[str, ...] = (".fake",)
    needs_seekable = False

    def __init__(self, name: str, score: float) -> None:
        self.name, self._score = name, score

    def detect(self, head, filename):  # type: ignore[no-untyped-def]
        return self._score

    def convert(self, stream, opts, filename):  # type: ignore[no-untyped-def]
        return Document([])


def test_builtin_text_registered():
    assert "text" in [c.name for c in registry.converters()]


def test_magic_beats_extension_beats_sniff(custom):
    for conv in (Fake("magic", registry.MAGIC), Fake("ext", registry.EXTENSION)):
        registry.register(conv)
        custom.append(conv.name)
    assert registry.detect(b"x", "f.fake")[0].name == "magic"  # type: ignore[union-attr]
    registry.unregister("magic")
    assert registry.detect(b"x", "f.fake")[0].name == "ext"  # type: ignore[union-attr]
    registry.unregister("ext")
    assert registry.detect(b"x", "f.fake")[0].name == "text"  # type: ignore[union-attr]


def test_ties_go_to_first_registered(custom):
    for n in ("first", "second"):
        registry.register(Fake(n, 5.0))
        custom.append(n)
    assert registry.detect(b"x", None)[0].name == "first"  # type: ignore[union-attr]


def test_duplicate_registration_rejected(custom):
    registry.register(Fake("dup", 1.0))
    custom.append("dup")
    with pytest.raises(ValueError):
        registry.register(Fake("dup", 1.0))
    registry.register(Fake("dup", 2.0), replace=True)


def test_forced_type_and_unknown_type():
    assert registry.detect(b"\x00\x01", None, forced="text")[0].name == "text"  # type: ignore[union-attr]
    with pytest.raises(ValueError, match="unknown type"):
        registry.detect(b"x", None, forced="nope")


def test_binary_with_no_matching_converter_is_none():
    assert registry.detect(b"\x00\x01\x02", "blob.bin")[0] is None


def test_buggy_detect_becomes_note_not_crash(custom):
    class Bad(Fake):
        def detect(self, head, filename):  # type: ignore[no-untyped-def]
            raise RuntimeError("kaput")

    registry.register(Bad("bad", 0))
    custom.append("bad")
    conv, notes = registry.detect(b"hello", "a.txt")
    assert conv.name == "text"  # type: ignore[union-attr]
    assert [n.code for n in notes] == ["detect-error"]
