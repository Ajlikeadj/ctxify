"""CSV/TSV converter: fidelity, detection and robustness."""

from __future__ import annotations

import json

import ctxify


def convert(data: bytes, *, name: str | None = None, **options):  # type: ignore[no-untyped-def]
    options.setdefault("token_backend", "chars")
    return ctxify.convert(data, name=name, options=ctxify.Options(**options))


def codes(result) -> list[str]:  # type: ignore[no-untyped-def]
    return [w.code for w in result.warnings]


def table(result):  # type: ignore[no-untyped-def]
    """The single Table block of a json-compact conversion."""
    payload = json.loads(result.text)
    tables = [block for block in payload if block.get("type") == "table"]
    assert len(tables) == 1
    return tables[0]


def test_small_table_renders_as_markdown():
    r = convert(b"name,qty\nwidget,3\n", name="a.csv")
    assert r.text == "| name | qty |\n|---|---|\n| widget | 3 |\n"
    assert r.warnings == ()


def test_forced_csv_style_is_a_fenced_block():
    r = convert(b"a,b\n1,2\n", name="a.csv", table_style="csv")
    assert r.text == "```csv\na,b\n1,2\n```\n"


def test_records_style_omits_empty_cells():
    r = convert(b"a,b,c\n1,,3\n", name="a.csv", table_style="records")
    assert r.text == "a: 1\nc: 3\n"


def test_tsv_extension_uses_tabs_even_when_values_contain_commas():
    r = convert(b"name\tdesc\nwidget\ta,b\n", name="a.tsv", format="json-compact")
    assert table(r)["header"] == ["name", "desc"]
    assert table(r)["rows"] == [["widget", "a,b"]]


def test_semicolon_delimiter_is_detected():
    r = convert(b"name;qty\nx;1\ny;2\n", name="a.csv", format="json-compact")
    assert table(r)["header"] == ["name", "qty"]
    assert table(r)["rows"] == [["x", "1"], ["y", "2"]]


def test_pipe_delimiter_is_detected():
    r = convert(b"a|b\n1|2\n", name="x.txt", type="csv", format="json-compact")
    assert table(r)["header"] == ["a", "b"]


def test_quoted_field_with_newline_and_comma_is_preserved():
    r = convert(b'name,note\nx,"line1\nline2, ok"\n', name="a.csv", format="json-compact")
    assert table(r)["rows"] == [["x", "line1\nline2, ok"]]


def test_ragged_rows_are_padded_and_reported():
    r = convert(b"a,b,c\n1,2\n3,4,5\n", name="a.csv", format="json-compact")
    assert table(r)["rows"] == [["1", "2", ""], ["3", "4", "5"]]
    assert codes(r) == ["ragged-rows"]
    assert r.warnings[0].severity == "repair"


def test_duplicate_rows_are_preserved():
    r = convert(b"a,b\n1,2\n1,2\n1,2\n", name="a.csv", format="json-compact")
    assert table(r)["rows"] == [["1", "2"]] * 3
    assert "duplicate-rows" not in codes(r)


def test_short_numeric_rows_are_preserved():
    # Regression: the old converter treated rows of <=3-digit numbers as "junk" footers.
    r = convert(b"id\n1\n2\n3\n", name="a.csv", format="json-compact")
    assert table(r)["rows"] == [["1"], ["2"], ["3"]]


def test_header_only_file():
    r = convert(b"a,b\n", name="a.csv")
    assert r.text == "| a | b |\n|---|---|\n"
    assert table(convert(b"a,b\n", name="a.csv", format="json-compact"))["rows"] == []


def test_numeric_first_row_is_not_a_header():
    r = convert(b"1,2\n3,4\n", name="a.csv", format="json-compact")
    assert "header" not in table(r)
    assert table(r)["rows"] == [["1", "2"], ["3", "4"]]


def test_blank_lines_are_ignored():
    r = convert(b"a,b\n\n1,2\n\n\n3,4\n", name="a.csv", format="json-compact")
    assert table(r)["rows"] == [["1", "2"], ["3", "4"]]


def test_header_with_empty_names_gets_generated_names():
    r = convert(b"a,,c\n1,2,3\n", name="a.csv", format="json-compact")
    assert table(r)["header"] == ["a", "col2", "c"]


def test_short_header_is_extended():
    r = convert(b"a,b\n1,2,3\n4,5,6\n", name="a.csv", format="json-compact")
    assert table(r)["header"] == ["a", "b", "col3"]
    assert "header-padded" in codes(r)


def test_whole_file_is_read_not_just_the_first_block():
    # Regression: the old converter parsed only the first 64 KiB.
    body = "id,value\n" + "".join(f"{i},{i * 2}\n" for i in range(20_000))
    data = body.encode()
    assert len(data) > 64 * 1024
    r = convert(data, name="big.csv", format="json-compact")
    assert len(table(r)["rows"]) == 20_000


def test_budget_summary_includes_column_stats_and_sample():
    data = b"a,b\n" + b"".join(f"{i},x{i}\n".encode() for i in range(30))
    r = convert(data, name="a.csv", budget="summary")
    assert r.text.startswith("columns: a(integer), b(string); rows: 30; null rate: 0.0%\n")
    assert "[... 10 more table rows]" in r.text


def test_null_rate_is_computed_over_rows_and_columns():
    r = convert(b"a,b\n1,\n2,3\n", name="a.csv", budget="summary")
    assert "rows: 2; null rate: 25.0%" in r.text


def test_utf16_input_with_bom():
    data = "a,b\n1,2\n".encode("utf-16")
    r = convert(data, name="a.csv", format="json-compact")
    assert table(r)["rows"] == [["1", "2"]]
    assert r.warnings == ()


def test_utf8_bom_is_stripped():
    r = convert(b"\xef\xbb\xbfa,b\n1,2\n", name="a.csv", format="json-compact")
    assert table(r)["header"] == ["a", "b"]


def test_cp1252_is_repaired_with_a_warning():
    r = convert(b"name,city\ncaf\xe9,z\xfcrich\n", name="a.csv")
    assert "caf" in r.text and "\ufffd" not in r.text
    assert "encoding-guessed" in codes(r)


def test_oversized_field_becomes_a_warning_not_a_crash():
    data = b'a,b\nx,"' + b"z" * 200_000 + b'"\n'
    r = convert(data, name="a.csv")
    assert "csv-error" in codes(r)
    assert r.warnings[-1].severity == "skip"


def test_whitespace_only_file_is_empty():
    r = convert(b"\n \n\t\n", name="a.csv")
    assert r.text == ""
    assert codes(r) == ["empty-input"]


def test_conversion_is_deterministic():
    data = b"a,b\n1,2\n" * 50
    outputs = {convert(data, name="a.csv").text for _ in range(5)}
    assert len(outputs) == 1
