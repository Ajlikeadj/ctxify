from __future__ import annotations

import subprocess
import sys

import pytest

from ctxify.cli import main


def run(*args: str, stdin: bytes | None = None) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        [sys.executable, "-m", "ctxify", *args], input=stdin, capture_output=True, check=False
    )


def test_stdin_pipeline_end_to_end():
    p = run("-", "--type", "text", "--token-backend", "chars", stdin=b"hello   there\n")
    assert (p.returncode, p.stdout) == (0, b"hello there\n")


def test_utf8_output_regardless_of_locale():
    p = run("-", "--type", "text", stdin="日本語 é\n".encode())
    assert p.stdout == "日本語 é\n".encode()


def test_version_and_list_types():
    assert run("--version").stdout.startswith(b"ctxify ")
    assert b"text\t.txt" in run("--list-types").stdout


def test_no_input_is_usage_error():
    assert run().returncode == 2


def test_multiple_inputs_require_out(tmp_path):
    a = tmp_path / "a.txt"
    a.write_text("x")
    assert run(str(a), str(a)).returncode == 2


def test_out_dir_names_and_collisions(tmp_path, capsys):
    (tmp_path / "d1").mkdir()
    (tmp_path / "d2").mkdir()
    a, b = tmp_path / "d1" / "a.txt", tmp_path / "d2" / "a.txt"
    a.write_text("first")
    b.write_text("second")
    out = tmp_path / "out"
    assert main([str(a), str(b), "--out", str(out), "-q"]) == 0
    assert sorted(p.name for p in out.iterdir()) == ["a.txt-2.md", "a.txt.md"]
    assert (out / "a.txt.md").read_text() == "first\n"
    assert (out / "a.txt-2.md").read_text() == "second\n"


def test_missing_file_exit_2_and_others_still_processed(tmp_path, capsys):
    good = tmp_path / "g.txt"
    good.write_text("ok")
    out = tmp_path / "o"
    code = main([str(tmp_path / "missing.txt"), str(good), "--out", str(out), "-q"])
    assert code == 2 and (out / "g.txt.md").exists()
    assert "missing.txt" in capsys.readouterr().err


def test_strict_failure_exit_1(tmp_path, capsys):
    f = tmp_path / "bad.txt"
    f.write_bytes(b"caf\xe9\n")
    assert main([str(f), "--strict"]) == 1
    assert "encoding-guessed" in capsys.readouterr().err


def test_warnings_go_to_stderr_and_quiet_silences(tmp_path, capsys):
    f = tmp_path / "bad.txt"
    f.write_bytes(b"caf\xe9\n")
    assert main([str(f)]) == 0
    cap = capsys.readouterr()
    assert "repair[encoding-guessed]" in cap.err and "caf" in cap.out
    assert main([str(f), "-q"]) == 0
    assert capsys.readouterr().err == ""


def test_unknown_type_exit_2(tmp_path, capsys):
    f = tmp_path / "a.txt"
    f.write_text("x")
    assert main([str(f), "-t", "nope"]) == 2
    assert "unknown type" in capsys.readouterr().err


def test_bad_format_rejected_by_argparse(tmp_path):
    with pytest.raises(SystemExit) as ei:
        main(["x", "-f", "docx"])
    assert ei.value.code == 2
