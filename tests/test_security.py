"""Security-focused tests: output paths, front-matter injection, XXE and zip bombs."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

import ctxify
from ctxify.cli import _write_file


def test_output_file_cannot_escape_the_out_dir(tmp_path):
    out = tmp_path / "out"
    for hostile in ("../evil.txt", "../../evil.txt", "..\\..\\evil.txt"):
        target = _write_file("body", hostile, out, "md", set())
        assert target.parent.resolve() == out.resolve()
        assert target.name == Path(hostile).name + ".md"


def test_front_matter_cannot_be_injected_by_a_source_name():
    yaml = pytest.importorskip("yaml")
    name = "report\nmalicious: true\nsource: hacked.txt"
    r = ctxify.convert(b"body", name=name, front_matter=True, token_backend="chars")
    front = r.text.split("---\n\n", 1)[0].removeprefix("---\n")
    parsed = yaml.safe_load(front)
    assert parsed["source"] == name
    assert "malicious" not in parsed
    assert set(parsed) == {"source", "type", "tokens", "token_backend"}


def test_front_matter_cannot_be_injected_by_a_warning_message():
    from ctxify.ir import ConversionWarning, Document, Meta, Paragraph

    class Evil:
        name, extensions, needs_seekable = "evil", (".evil",), False

        def detect(self, head, filename):  # type: ignore[no-untyped-def]
            return 1.0 if filename and filename.endswith(".evil") else 0.0

        def convert(self, stream, opts, filename):  # type: ignore[no-untyped-def]
            return Document(
                [Paragraph("x")],
                Meta(),
                [ConversionWarning("evil", "x\nbad: true", "repair")],
            )

    from ctxify import registry

    registry.register(Evil(), replace=True)
    try:
        r = ctxify.convert(b"x", name="a.evil", front_matter=True, token_backend="chars")
        yaml = pytest.importorskip("yaml")
        front = r.text.split("---\n\n", 1)[0].removeprefix("---\n")
        parsed = yaml.safe_load(front)
        assert "bad" not in parsed
    finally:
        registry.unregister("evil")


def test_xxe_in_footnotes_is_not_expanded():
    docx = pytest.importorskip("docx")
    doc = docx.Document()
    doc.add_paragraph("body")
    buffer = io.BytesIO()
    doc.save(buffer)

    evil = b"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE w:footnotes [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>
<w:footnotes xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:footnote w:id="1"><w:p><w:r><w:t>&xxe;</w:t></w:r></w:p></w:footnote>
</w:footnotes>
"""
    source = zipfile.ZipFile(io.BytesIO(buffer.getvalue()))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        for info in source.infolist():
            archive.writestr(info, source.read(info.filename))
        archive.writestr("word/footnotes.xml", evil)

    r = ctxify.convert(out.getvalue(), name="a.docx", token_backend="chars")
    assert "root:" not in r.text
    assert "file://" not in r.text
    assert "footnote" not in r.text


def test_docx_zip_bomb_is_refused():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", b"A" * (8 * 1024 * 1024))
    r = ctxify.convert(buffer.getvalue(), name="bomb.docx")
    assert r.text == ""
    assert [w.code for w in r.warnings] == ["docx-invalid"]


def test_cli_stdout_cannot_crash_on_unencodable_text():
    # A lone surrogate cannot be UTF-8 encoded; the CLI must replace it, not traceback.
    import subprocess
    import sys

    p = subprocess.run(
        [sys.executable, "-m", "ctxify", "-", "--type", "json", "--token-backend", "chars"],
        input=b'{"x": "\\ud800"}',
        capture_output=True,
        check=False,
    )
    assert p.returncode == 0, p.stderr
    assert b"\xef\xbf\xbd" in p.stdout
