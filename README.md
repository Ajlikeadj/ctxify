# ctxify

**ctxify** is a lightweight CLI and importable Python library that turns enterprise files
into clean, token-optimized context for LLMs. It favours robustness over cleverness: bad
input becomes a warning plus best-effort output, never a traceback.

```text
text / logs  ·  CSV / TSV  ·  JSON / JSONL  ·  DOCX
```

## Installation

From a checkout (the normal route until the package is published on PyPI):

```bash
pip install -e ".[all]"   # everything
pip install -e .           # core only, stdlib-only
```

The optional extras are also installable individually once published:

```bash
pip install "ctxify[tokens]"   # tiktoken for exact token counts
pip install "ctxify[csv]"      # charset-normalizer for better encoding guesses
pip install "ctxify[docx]"     # python-docx + defusedxml
```

Python 3.10+ is required. The core package is stdlib-only; heavy parsers live in extras.

## Quickstart

```bash
ctxify report.docx --format md
ctxify data.csv --format md --max-tokens 4000
ctxify slides.json --front-matter
cat sales.csv | ctxify --type csv -
```

## CLI reference

| Option | Description |
|--------|-------------|
| `--format` | Output format: `md` (default), `json-compact`, `yaml`, `text` |
| `--table-style` | `auto` (default), `markdown`, `csv`, `records` (`keyvalue` is an alias) |
| `--max-tokens N` | Hard token budget; progressive strategies run until the body fits |
| `--front-matter` | Emit YAML front matter with source, type, tokens and warnings |
| `--strict` | Fail with exit code 1 instead of repairing or skipping |
| `--type` | Force a converter: `text`, `csv`, `json`, `docx` |
| `--budget` | `full` (default) or `summary` (column stats + sample) |
| `--token-backend` | `auto` (default), `tiktoken`, `chars` |
| `--out DIR` | Write one file per input into `DIR` |
| `--list-types` | List converters and exit |
| `--version` | Print the version |

Boolean flags also accept an explicit value: `--front-matter=True`, `--strict=False`.

Exit codes: `0` success (warnings allowed), `1` `--strict` failure, `2` usage or I/O error.

## Library API

```python
import ctxify

result = ctxify.convert("report.docx", format="md", front_matter=True, max_tokens=4000)
print(result.text)          # rendered body (+ front matter)
print(result.warnings)      # tuple of ConversionWarning
print(result.stats)         # tokens, token_backend, blocks, converter, dropped

# Bytes, streams and stdin-like objects work too
ctxify.convert(data, name="report.csv")
ctxify.convert(open("a.json", "rb"), format="yaml")
```

`ctxify.convert` raises only for caller errors (`TypeError` for an unsupported source,
`ValueError` for a bad option or unknown type, `OSError` for a missing file), an unusable
explicit tokenizer, and `ConversionError` in `strict` mode.

## What each converter guarantees

### Text / logs
Detects BOMs and encodings, strips control characters, collapses whitespace runs and
breadline runs, and keeps line structure inside a paragraph.

### CSV / TSV
- Parses the **whole file** with a real CSV reader, including quoted newlines.
- Detects `, ; \t |` from the content (and `.tsv` from the extension).
- Reports ragged rows and pads them instead of truncating data.
- **Never** de-duplicates rows or filters "suspicious" rows: conversion must not change
  the data. Structural problems are warnings.
- `--budget summary` emits column type inference, row count and null rate plus a sample.

### JSON / JSONL
- Parses the whole document; a single object becomes key/value pairs, an array of objects
  becomes a table with the union of keys, an array of strings becomes a list, and anything
  nested stays a lossless `Data` block for JSON/YAML output.
- Reports duplicate keys and non-standard `NaN`/`Infinity` constants instead of hiding them.
- JSONL skips only the invalid lines and says how many.

### DOCX
- Document order is preserved (paragraphs and tables interleaved as authored).
- Headings, nested tables, inline hyperlinks (`[text](url)`), footnotes and tracked changes.
- Tracked insertions are accepted; tracked deletions are excluded, with a warning.
- The ZIP container is checked for zip-bomb sizes/ratios before parsing, and footnotes are
  parsed with `defusedxml` when available.

## Token optimization

`--table-style auto` picks per table: `records` for sparse data, Markdown for small tables,
compact CSV for large ones.

When `--max-tokens N` is exceeded, these strategies run in order until the rendered body
fits, and `stats.dropped` / front matter record what happened:

1. Remove empty blocks and collapse consecutive duplicates
2. Abbreviate long cell and value strings
3. Sample oversized tables and key/value lists (with visible `[... N more]` elisions)
4. Render tables as compact CSV
5. Keep table headers only
6. Drop whole trailing blocks
7. Truncate the final block, closing any open code fence

Token counting uses `tiktoken` (`o200k_base`) when installed and the backend name is always
reported, because exact and heuristic counts differ.

## Front matter

```yaml
---
source: report.docx
type: docx
tokens: 2400
token_backend: tiktoken:o200k_base
warnings:
  - "tracked-changes: 3 tracked change(s): insertions accepted, deletions ignored."
---
```

## Development

```bash
python -m pip install -e ".[dev]"
python -m pytest
python -m ruff check .
python -m mypy
```

## License

MIT — see [LICENSE](LICENSE).
