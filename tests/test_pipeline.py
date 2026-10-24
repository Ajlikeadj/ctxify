from __future__ import annotations

import pytest

import ctxify
from ctxify import ConversionError, registry
from ctxify.ir import ConversionWarning, Document, Meta, Paragraph


def codes(result):
    return [w.code for w in result.warnings]


def test_zero_byte_input():
    r = ctxify.convert(b"", name="a.txt")
    assert r.text == "" and codes(r) == ["empty-input"] and r.stats.tokens == 0


def test_zero_byte_file_with_front_matter(tmp_path):
    p = tmp_path / "empty.csv"
    p.write_bytes(b"")
    r = ctxify.convert(p, front_matter=True, token_backend="chars")
    assert r.text.startswith("---\nsource: empty.csv\n") and "empty-input" in r.text


def test_binary_garbage_is_skipped_not_crashed():
    r = ctxify.convert(bytes(range(256)) * 4, name="blob.bin")
    assert r.text == "" and codes(r) == ["unsupported-type"] and r.stats.converter is None


def test_text_cleanup():
    raw = b"Hello   world \t\n\n\n\n  indented\x00\x07   text\n\nlast"
    r = ctxify.convert(raw, name="a.txt")
    assert r.text == "Hello world\n\nindented text\n\nlast\n"
    assert r.warnings == ()


def test_bytes_path_and_stream_inputs_agree(tmp_path, pipe):
    data = b"one\n\ntwo\n"
    p = tmp_path / "x.txt"
    p.write_bytes(data)
    with p.open("rb") as fh:
        a = ctxify.convert(p).text
        b = ctxify.convert(data, name="x.txt").text
        c = ctxify.convert(fh).text
    d = ctxify.convert(pipe(data), name="x.txt").text  # non-seekable
    assert a == b == c == d == "one\n\ntwo\n"


def test_stream_position_is_respected(tmp_path):
    import io

    s = io.BytesIO(b"SKIPhello\n")
    s.seek(4)
    assert ctxify.convert(s, name="a.txt").text == "hello\n"


def test_utf8_bom_and_utf16():
    assert ctxify.convert(b"\xef\xbb\xbfhi\n", name="a.txt").text == "hi\n"
    r = ctxify.convert("héllo\nwörld\n".encode("utf-16"), name="a.txt")
    assert r.text == "héllo\nwörld\n" and r.warnings == ()


def test_wrong_encoding_is_repaired_with_warning():
    r = ctxify.convert(b"caf\xe9 cr\xe8me\n", name="a.txt")
    assert "caf" in r.text and "\ufffd" not in r.text
    assert codes(r) == ["encoding-guessed"] and r.warnings[0].severity == "repair"


def test_invalid_byte_after_clean_utf8_sample_is_flagged():
    data = b"ok line\n" * 10_000 + b"bad \xff byte\n"  # sample (64 KiB) is clean UTF-8
    r = ctxify.convert(data, name="a.txt")
    assert "invalid-bytes" in codes(r)


def test_determinism():
    data = b"a  b\n\nc\n" * 50
    runs = {ctxify.convert(data, name="a.txt", token_backend="chars").text for _ in range(3)}
    assert len(runs) == 1


def test_front_matter_content_md():
    r = ctxify.convert(b"hi\n", name="a.txt", front_matter=True, token_backend="chars")
    assert (
        r.text == "---\nsource: a.txt\ntype: text\ntokens: 1\ntoken_backend: chars/4\n---\n\nhi\n"
    )


def test_front_matter_has_no_directories(tmp_path):
    p = tmp_path / "sub" / "doc.txt"
    p.parent.mkdir()
    p.write_text("x")
    assert "source: doc.txt\n" in ctxify.convert(p, front_matter=True).text


def test_format_options():
    assert ctxify.convert(b"hi\n", name="a.txt", format="json-compact").text == (
        '[{"type":"paragraph","text":"hi"}]\n'
    )
    assert ctxify.convert(b"hi\n", name="a.txt", format="text").text == "hi\n"


def test_bad_options():
    with pytest.raises(ValueError):
        ctxify.convert(b"x", format="docx")  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        ctxify.convert(b"x", bogus=1)
    with pytest.raises(ValueError, match="unknown type"):
        ctxify.convert(b"x", type="nope")
    with pytest.raises(TypeError):
        ctxify.convert(123)  # type: ignore[arg-type]


def test_missing_file_raises_oserror(tmp_path):
    with pytest.raises(FileNotFoundError):
        ctxify.convert(tmp_path / "nope.txt")


class Boom:
    name, extensions, needs_seekable = "boom", (".boom",), False

    def detect(self, head, filename):
        return 2.0 if filename and filename.endswith(".boom") else 0.0

    def convert(self, stream, opts, filename):
        def gen():
            yield Paragraph("partial")
            raise RuntimeError("kaput")

        return Document(gen(), Meta())


class BoomEarly(Boom):
    name, extensions = "boom-early", (".early",)

    def detect(self, head, filename):
        return 2.0 if filename and filename.endswith(".early") else 0.0

    def convert(self, stream, opts, filename):
        raise RuntimeError("early kaput")


def test_converter_crash_midstream_keeps_partial_output(custom):
    registry.register(Boom())
    custom.append("boom")
    r = ctxify.convert(b"x", name="f.boom")
    assert r.text == "partial\n"
    assert codes(r) == ["converter-error"] and "Kept 1 block" in r.warnings[0].message


def test_converter_crash_at_start_gives_empty_output(custom):
    registry.register(BoomEarly())
    custom.append("boom-early")
    r = ctxify.convert(b"x", name="f.early")
    assert r.text == "" and codes(r) == ["converter-error"]


def test_recursion_error_is_contained(custom):
    class Deep(Boom):
        name, extensions = "deep", (".deep",)

        def detect(self, head, filename):
            return 2.0 if filename and filename.endswith(".deep") else 0.0

        def convert(self, stream, opts, filename):
            def f():
                return f()

            return Document([Paragraph(str(f()))])

    registry.register(Deep())
    custom.append("deep")
    assert codes(ctxify.convert(b"x", name="a.deep")) == ["converter-error"]


@pytest.mark.parametrize("data,name", [(b"\x00\x01\x02" * 10, "a.bin"), (b"x", "f.boom")])
def test_strict_raises_on_skips(data, name, custom):
    registry.register(Boom())
    custom.append("boom")
    with pytest.raises(ConversionError) as ei:
        ctxify.convert(data, name=name, strict=True)
    assert ei.value.warnings


def test_strict_allows_info_only_and_clean_input():
    assert ctxify.convert(b"", name="a.txt", strict=True).text == ""
    assert ctxify.convert(b"fine\n", name="a.txt", strict=True).text == "fine\n"


def test_strict_raises_on_repair():
    with pytest.raises(ConversionError, match="encoding-guessed"):
        ctxify.convert(b"caf\xe9\n", name="a.txt", strict=True)


class NeedsSeek(Boom):
    name, extensions, needs_seekable = "needs-seek", (".seek",), True

    def detect(self, head, filename):
        return 2.0 if filename and filename.endswith(".seek") else 0.0

    def convert(self, stream, opts, filename):
        assert stream.seekable()
        stream.seek(0, 2)
        size = stream.tell()
        stream.seek(0)
        return Document([Paragraph(f"{size}:{stream.read().decode()}")])


def test_non_seekable_input_is_buffered_for_converters_that_need_it(custom, pipe):
    registry.register(NeedsSeek())
    custom.append("needs-seek")
    assert ctxify.convert(pipe(b"abc"), name="f.seek").text == "3:abc\n"


def test_buffer_limit_gives_skip_warning(custom, pipe):
    registry.register(NeedsSeek())
    custom.append("needs-seek")
    r = ctxify.convert(pipe(b"x" * 100), name="f.seek", max_input_bytes=10)
    assert r.text == "" and codes(r) == ["input-too-large"]


def test_converter_warnings_flow_into_result_and_front_matter(custom):
    class Warny(Boom):
        name, extensions = "warny", (".warny",)

        def detect(self, head, filename):
            return 2.0 if filename and filename.endswith(".warny") else 0.0

        def convert(self, stream, opts, filename):
            return Document(
                [Paragraph("p")],
                Meta(pages=3),
                [ConversionWarning("trimmed", "cut 2 rows", "repair")],
            )

    registry.register(Warny())
    custom.append("warny")
    r = ctxify.convert(b"x", name="f.warny", front_matter=True, token_backend="chars")
    assert (
        "pages: 3\n" in r.text and "warnings:\n  - " in r.text and "trimmed: cut 2 rows" in r.text
    )
    assert r.warnings[0].severity == "repair"
