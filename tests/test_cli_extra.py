"""CLI end-to-end tests for documented options and exit codes."""

from __future__ import annotations

import subprocess
import sys

import pytest

from ctxify.cli import main
from ctxify.tokens import count_tokens


def run(*args: str, stdin: bytes | None = None) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        [sys.executable, "-m", "ctxify", *args], input=stdin, capture_output=True, check=False
    )


def test_max_tokens_is_enforced_end_to_end():
    p = run(
        "-",
        "--type",
        "text",
        "--max-tokens",
        "2",
        "--token-backend",
        "chars",
        stdin=b"a b c d e f g\n",
    )
    assert p.returncode == 0
    assert count_tokens(p.stdout.decode(), "chars") <= 2


def test_max_tokens_zero_is_a_usage_error():
    p = run("-", "--max-tokens", "0", stdin=b"x")
    assert p.returncode == 2
    assert b"max_tokens" in p.stderr


def test_table_style_keyvalue_alias(tmp_path):
    path = tmp_path / "a.csv"
    path.write_text("a,b\n1,2\n")
    p = run(str(path), "--table-style", "keyvalue", "--token-backend", "chars")
    assert p.stdout == b"a: 1\nb: 2\n"


def test_table_style_records(tmp_path):
    path = tmp_path / "a.csv"
    path.write_text("a,b\n1,2\n")
    assert run(str(path), "--table-style", "records").stdout == b"a: 1\nb: 2\n"


def test_budget_summary(tmp_path):
    path = tmp_path / "a.csv"
    path.write_text("a,b\n1,2\n", encoding="utf-8", newline="")
    p = run(str(path), "--budget", "summary", "--token-backend", "chars")
    assert p.stdout.startswith(b"columns: a(integer), b(integer); rows: 1")


def test_front_matter_with_explicit_true(tmp_path):
    path = tmp_path / "a.txt"
    path.write_text("hi")
    p = run(str(path), "--front-matter=True", "--token-backend", "chars")
    assert p.stdout.startswith(b"---\nsource: a.txt\n")


def test_front_matter_with_explicit_false(tmp_path):
    path = tmp_path / "a.txt"
    path.write_text("hi")
    p = run(str(path), "--front-matter=False", "--token-backend", "chars")
    assert p.stdout == b"hi\n"


def test_front_matter_flag_does_not_swallow_the_next_argument(tmp_path):
    path = tmp_path / "a.txt"
    path.write_text("hi")
    p = run("--front-matter", str(path), "--token-backend", "chars")
    assert p.returncode == 0 and p.stdout.startswith(b"---\n")


def test_strict_false_allows_repairs(tmp_path):
    path = tmp_path / "a.txt"
    path.write_bytes(b"caf\xe9\n")
    assert run(str(path), "--strict=False").returncode == 0


def test_list_types_includes_all_builtins():
    p = run("--list-types")
    assert p.returncode == 0
    for name in (b"text\t.txt", b"csv\t.csv", b"json\t.json", b"docx\t.docx"):
        assert name in p.stdout


def test_out_dir_with_stdin(tmp_path):
    p = run(
        "-",
        "--type",
        "text",
        "--out",
        str(tmp_path),
        "--token-backend",
        "chars",
        stdin=b"hi\n",
    )
    assert p.returncode == 0
    assert (tmp_path / "stdin.md").read_text(encoding="utf-8") == "hi\n"


def test_out_extension_follows_format(tmp_path):
    source = tmp_path / "a.txt"
    source.write_text("hi")
    out = tmp_path / "out"
    assert main([str(source), "-f", "json-compact", "--out", str(out), "-q"]) == 0
    assert (out / "a.txt.json").exists()


def test_quiet_suppresses_filesystem_noise(tmp_path, capsys):
    source = tmp_path / "a.txt"
    source.write_text("hi")
    out = tmp_path / "out"
    assert main([str(source), "--out", str(out), "-q"]) == 0
    assert capsys.readouterr().err == ""


def test_help_exits_zero():
    p = run("--help")
    assert p.returncode == 0
    assert b"--max-tokens" in p.stdout and b"--table-style" in p.stdout


def test_bad_table_style_exits_two():
    p = run("-", "--table-style", "nope", stdin=b"x")
    assert p.returncode == 2


def test_bad_budget_exits_two():
    p = run("-", "--budget", "nope", stdin=b"x")
    assert p.returncode == 2


def test_tokenizer_unavailable_exits_two(monkeypatch, tmp_path):
    from ctxify import tokens

    def boom(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise tokens.TokenizerUnavailable("no")

    monkeypatch.setattr(tokens, "get_counter", boom)
    source = tmp_path / "a.txt"
    source.write_text("hi")
    assert main([str(source), "--token-backend", "tiktoken"]) == 2


def test_invalid_bool_value_is_a_usage_error():
    with pytest.raises(SystemExit) as info:
        main(["-", "--strict=maybe"])
    assert info.value.code == 2


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"\x00" * 10,
        b"{bad json",
        b'"unterminated',
        b"\xff\xfeodd",
        b"PK\x03\x04junk",
        bytes(range(256)),
        b"a,b\n" + b"1," * 5000,
    ],
)
@pytest.mark.parametrize(
    "flags",
    [[], ["--strict"], ["-f", "yaml"], ["-f", "json-compact"], ["--max-tokens", "3"]],
)
def test_cli_never_prints_a_traceback(data, flags, tmp_path):
    path = tmp_path / "input.bin"
    path.write_bytes(data)
    p = run(str(path), *flags)
    assert p.returncode in (0, 1, 2)
    assert b"Traceback" not in p.stderr
    assert b"Traceback" not in p.stdout
