"""JSON/JSONL converter: structure choice, fidelity and malformed input."""

from __future__ import annotations

import json

import ctxify


def convert(data: bytes, *, name: str | None = None, **options):  # type: ignore[no-untyped-def]
    options.setdefault("token_backend", "chars")
    return ctxify.convert(data, name=name, options=ctxify.Options(**options))


def codes(result) -> list[str]:  # type: ignore[no-untyped-def]
    return [w.code for w in result.warnings]


def table(result):  # type: ignore[no-untyped-def]
    payload = json.loads(result.text)
    tables = [block for block in payload if block.get("type") == "table"]
    assert len(tables) == 1
    return tables[0]


def test_flat_object_becomes_key_value():
    r = convert(b'{"name": "x", "n": 1}', name="a.json")
    assert r.text == "name: x\nn: 1\n"
    assert r.stats.converter == "json"


def test_array_of_objects_becomes_a_table_with_union_of_keys():
    r = convert(b'[{"a":1,"b":2},{"a":3,"c":4}]', name="a.json")
    assert r.text == "| a | b | c |\n|---|---|---|\n| 1 | 2 |  |\n| 3 |  | 4 |\n"


def test_union_keys_keep_first_seen_order():
    r = convert(b'[{"b":1,"a":2},{"c":3,"a":4}]', name="a.json", format="json-compact")
    assert table(r)["header"] == ["b", "a", "c"]


def test_array_of_strings_becomes_a_list():
    r = convert(b'["alpha", "beta"]', name="a.json")
    assert r.text == "- alpha\n- beta\n"


def test_nested_structure_stays_lossless():
    data = {"a": {"b": [1, 2]}, "c": None}
    r = convert(json.dumps(data).encode(), name="a.json", format="json-compact")
    assert json.loads(r.text) == data


def test_scalar_documents():
    assert convert(b"null", name="a.json").text == "```json\nnull\n```\n"
    assert convert(b"42", name="a.json", format="json-compact").text == "42\n"
    assert convert(b'"hi"', name="a.json", format="json-compact").text == '"hi"\n'


def test_empty_containers():
    assert convert(b"[]", name="a.json", format="json-compact").text == "[]\n"
    assert convert(b"{}", name="a.json", format="json-compact").text == "{}\n"


def test_cell_formatting_for_scalars():
    r = convert(
        b'[{"n":null,"b":true,"f":1.5,"i":2,"s":"x"}]',
        name="a.json",
        format="json-compact",
    )
    assert table(r)["rows"] == [["", "true", "1.5", "2", "x"]]


def test_whole_file_is_read_not_just_the_first_block():
    records = [{"id": i, "name": f"row-{i}"} for i in range(5000)]
    data = json.dumps(records).encode()
    assert len(data) > 64 * 1024
    r = convert(data, name="big.json", format="json-compact")
    assert len(table(r)["rows"]) == 5000


def test_jsonl_objects_become_a_table():
    r = convert(b'{"a":1}\n{"a":2}\n', name="a.jsonl", format="json-compact")
    assert table(r)["header"] == ["a"]
    assert table(r)["rows"] == [["1"], ["2"]]
    assert r.stats.converter == "json"


def test_jsonl_skips_only_invalid_lines():
    r = convert(b'{"a":1}\nnot json\n{"a":2}\n', name="a.jsonl", format="json-compact")
    assert table(r)["rows"] == [["1"], ["2"]]
    assert "jsonl-invalid-lines" in codes(r)
    assert r.warnings[0].severity == "repair"


def test_jsonl_keeps_valid_lines_when_most_lines_are_bad():
    r = convert(b'{"a":1}\nbroken\n', name="a.jsonl", format="json-compact")
    assert table(r)["rows"] == [["1"]]
    assert "jsonl-invalid-lines" in codes(r)


def test_jsonl_with_one_object_is_a_document_not_a_table():
    r = convert(b'{"a":1}', name="a.jsonl")
    assert r.text == "a: 1\n"


def test_jsonl_heterogeneous_lines_stay_lossless():
    r = convert(b'{"a":1}\n5\n', name="a.jsonl", format="json-compact")
    assert json.loads(r.text) == [{"a": 1}, 5]


def test_duplicate_keys_are_reported_and_last_value_wins():
    r = convert(b'{"a":1,"a":2}', name="a.json")
    assert r.text == "a: 2\n"
    assert "duplicate-keys" in codes(r)
    assert "a" in r.warnings[0].message


def test_nan_and_infinity_are_reported_and_json_stays_valid():
    r = convert(b'{"x": NaN, "y": [Infinity, -Infinity]}', name="a.json")
    assert "non-finite-number" in codes(r)
    payload = convert(
        b'{"x": NaN, "y": [Infinity, -Infinity]}', name="a.json", format="json-compact"
    )
    json.loads(payload.text)  # must not raise: output is valid JSON


def test_invalid_json_yields_no_output_and_a_skip_warning():
    r = convert(b'{"a": 1,,}', name="a.json")
    assert r.text == ""
    assert codes(r) == ["invalid-json"]
    assert r.warnings[0].severity == "skip"


def test_json_is_detected_by_content_without_a_filename():
    r = convert(b'  {"a": 1}')
    assert r.stats.converter == "json"
    assert r.text == "a: 1\n"


def test_extension_still_wins_over_a_json_sniff():
    r = convert(b'  {"a": 1}', name="payload.txt")
    assert r.stats.converter == "text"


def test_utf8_bom_is_handled():
    r = convert(b'\xef\xbb\xbf{"a":1}', name="a.json")
    assert r.text == "a: 1\n"


def test_utf16_json():
    r = convert('{"a":1}'.encode("utf-16"), name="a.json")
    assert r.text == "a: 1\n"


def test_deeply_nested_json_is_skipped_not_crashed():
    depth = 5_000
    r = convert(b"[" * depth + b"]" * depth, name="deep.json")
    assert r.text == ""
    assert codes(r) == ["json-too-deep"]
    assert r.warnings[0].severity == "skip"


def test_moderately_nested_json_is_preserved():
    depth = 100
    data = "[" * depth + "1" + "]" * depth
    r = convert(data.encode(), name="deep.json", format="json-compact")
    assert json.loads(r.text) == json.loads(data)


def test_jsonl_budget_summary_samples_rows():
    data = b"".join(b'{"a":%d}\n' % i for i in range(100))
    r = convert(data, name="a.jsonl", budget="summary")
    assert "[... 80 more table rows]" in r.text


def test_large_object_keeps_all_keys_as_keyvalue():
    data = json.dumps({f"k{i}": i for i in range(50)}).encode()
    r = convert(data, name="a.json", format="json-compact")
    pairs = json.loads(r.text)[0]["pairs"]
    assert len(pairs) == 50


def test_conversion_is_deterministic():
    data = json.dumps([{"a": i, "b": i * 2} for i in range(50)]).encode()
    outputs = {convert(data, name="a.json").text for _ in range(5)}
    assert len(outputs) == 1
