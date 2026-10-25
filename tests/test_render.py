from __future__ import annotations

import json

import pytest

from ctxify.ir import Code, Data, Elision, Heading, KeyValue, ListBlock, Paragraph, Table
from ctxify.render import add_front_matter, render_body
from ctxify.render.yaml import dump


def md(*blocks):
    return render_body(list(blocks), "md")


def test_heading_paragraph_spacing():
    assert md(Heading(2, "A\nB"), Paragraph("text  ")) == "## A B\n\ntext\n"


def test_empty_is_empty():
    assert md() == ""
    assert render_body([], "json-compact") == "[]\n"


def test_markdown_table_escapes_pipes_and_newlines_and_pads_ragged_rows():
    t = Table(("a", "b"), (("x|y", "line1\nline2"), ("only",)))
    assert md(t) == "| a | b |\n|---|---|\n| x\\|y | line1 line2 |\n| only |  |\n"


def test_table_without_header_uses_blank_header():
    assert md(Table(None, (("1", "2"),))).startswith("|  |  |\n|---|---|\n")


def test_csv_style_quotes_properly():
    out = md(Table(("a", "b"), (("x,y", 'q"z'),), style="csv"))
    assert out == '```csv\na,b\n"x,y","q""z"\n```\n'


def test_records_style_skips_empty_cells_and_names_missing_headers():
    t = Table(("name", ""), (("Ann", "x"), ("Bob", ""), ("", "")), style="records")
    assert md(t) == "name: Ann\ncol2: x\n\nname: Bob\n"


def test_table_caption():
    assert md(Table(("a",), (("1",),), caption="Sales")).startswith("Table: Sales\n| a |")


def test_code_fence_grows_past_inner_backticks():
    out = md(Code("x\n````\ny", "py"))
    assert out.startswith("`````py\n") and out.endswith("\n`````\n")


def test_nested_and_ordered_lists():
    nested = ListBlock(("a", ListBlock(("b", "c")), "d"))
    assert md(nested) == "- a\n  - b\n  - c\n- d\n"
    assert md(ListBlock(("x", "y"), ordered=True)) == "1. x\n2. y\n"


def test_multiline_list_item_is_indented():
    assert md(ListBlock(("a\nb",))) == "- a\n  b\n"


def test_keyvalue_data_elision():
    assert md(KeyValue((("k", "v"),))) == "k: v\n"
    assert md(Data({"a": [1]})) == '```json\n{"a":[1]}\n```\n'
    assert md(Elision(5, "rows")) == "[... 5 more rows]\n"


def test_text_format_has_no_markup():
    out = render_body(
        [Heading(1, "T"), Table(("a", "b"), (("1", "2"),)), ListBlock(("x",))], "text"
    )
    assert out == "T\n\na\tb\n1\t2\n\n- x\n"


def test_json_compact_lone_data_is_bare_value():
    assert render_body([Data({"a": 1, "b": None})], "json-compact") == '{"a":1,"b":null}\n'


def test_json_compact_blocks_and_unicode():
    out = json.loads(render_body([Heading(1, "é"), Paragraph("p")], "json-compact"))
    assert out == [
        {"type": "heading", "level": 1, "text": "é"},
        {"type": "paragraph", "text": "p"},
    ]


def test_json_keyvalue_duplicate_keys_become_pairs():
    out = json.loads(render_body([KeyValue((("a", "1"), ("a", "2")))], "json-compact"))
    assert out[0]["pairs"] == [["a", "1"], ["a", "2"]]


TRICKY = [
    "yes",
    "No",
    "null",
    "~",
    "1e3",
    "007",
    "a: b",
    "a #b",
    "#x",
    "- x",
    "",
    " pad ",
    "multi\nline",
    'quote"s',
    "colon:",
    "é ü 日本",
    "{x}",
    "[x]",
    "*star",
    "&anchor",
    "!tag",
    "@at",
    "%pct",
    "true",
    "2024-01-01",
    "tab\there",
    "back\\slash",
    "plain text",
    "snake_case.v2",
]


def test_yaml_round_trips_through_pyyaml():
    yaml = pytest.importorskip("yaml")
    data = {
        "strings": TRICKY,
        "nested": {"empty_list": [], "empty_dict": {}, "k: ey": [{"a": 1, "b": [1, 2]}, [3, [4]]]},
        "scalars": [1, -2, 1.5, True, False, None],
    }
    assert yaml.safe_load(dump(data)) == data
    for s in TRICKY:
        assert yaml.safe_load(dump({"k": s})) == {"k": s}, s
        assert yaml.safe_load(dump({s: 1})) == {s: 1}, s


def test_yaml_special_floats():
    yaml = pytest.importorskip("yaml")
    loaded = yaml.safe_load(dump([float("inf"), float("-inf")]))
    assert loaded == [float("inf"), float("-inf")]


def test_yaml_exponent_floats_round_trip():
    yaml = pytest.importorskip("yaml")
    for value in (1e-5, 1e20, -1.5e-9, 2.5e300):
        loaded = yaml.safe_load(dump(value))
        assert loaded == value and isinstance(loaded, float), (value, dump(value))


def test_yaml_control_and_line_separator_characters_are_escaped():
    yaml = pytest.importorskip("yaml")
    for value in ("\x00", "\x7f", "\u2028", "\u2029", "\ufeff", "a\nb"):
        assert yaml.safe_load(dump({"k": value})) == {"k": value}
        assert yaml.safe_load(dump({value: 1})) == {value: 1}


def test_front_matter_is_valid_for_machine_formats():
    import ctxify

    yaml = pytest.importorskip("yaml")
    data = b"a,b\n1,2\n"
    compact = ctxify.convert(
        data, name="a.csv", format="json-compact", front_matter=True, token_backend="chars"
    )
    parsed = json.loads(compact.text)
    assert set(parsed) == {"meta", "content"}
    assert parsed["meta"]["source"] == "a.csv"

    as_yaml = ctxify.convert(
        data, name="a.csv", format="yaml", front_matter=True, token_backend="chars"
    )
    documents = list(yaml.safe_load_all(as_yaml.text))
    assert len(documents) == 2
    assert documents[0]["source"] == "a.csv"
    assert documents[1][0]["type"] == "table"


def test_front_matter_variants():
    meta = {"source": "a.txt", "tokens": 3}
    assert add_front_matter("body\n", meta, "md") == "---\nsource: a.txt\ntokens: 3\n---\n\nbody\n"
    assert add_front_matter("[1]\n", meta, "yaml") == "---\nsource: a.txt\ntokens: 3\n---\n[1]\n"
    wrapped = json.loads(add_front_matter("[1]\n", meta, "json-compact"))
    assert wrapped == {"meta": meta, "content": [1]}
