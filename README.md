# ctxify

**ctxify** is a lightweight, fast CLI tool and importable Python library that converts enterprise files into clean, token-optimized context for LLMs. It focuses on robustness to bad input and minimizing token count without losing meaning.

## Installation

```bash
pip install ctxify
# Or with optional dependencies:
pip install ctxify[pdf,docx,ocr,xlsx]
```

## Quickstart

```bash
# Convert a DOCX file to Markdown
ctxify report.docx --format md

# Convert with token budget
ctxify data.csv --format md --max-tokens 4000

# Add front matter
ctxify slides.pptx --front-matter=True

# Stream from stdin
cat sales.csv | ctxify --type csv -
```

## CLI Reference

| Option | Description |
|--------|-------------|
| `--format` | Output format: `md` (default), `json-compact`, `yaml`, `text` |
| `--table-style` | Table representation: `markdown`, `csv`, `keyvalue` |
| `--max-tokens N` | Hard token budget; apply progressive strategies if exceeded |
| `--front-matter` | Emit YAML front matter with source info and warnings |
| `--strict` | Fail on errors instead of best-effort recovery |
| `--type` | Force input type: `csv`, `json`, `jsonl`, `docx`, `pdf` |
| `--budget` | CSV budget setting: `summary` (header stats + sample) or `full` |

## Token Optimization

ctxify applies the following strategies to minimize token count:

- **Boilerplate stripping**: Removes repeated headers/footers, page numbers, whitespace noise
- **Table optimization**: Auto-selects the most token-efficient representation:
  - Markdown tables for small tables
  - Compact CSV-in-codeblock for larger tables
  - Key:value records for sparse data
- **JSON flattening/summarization**: 
  - Collapses deep nesting into schema + rows form
  - Truncates long arrays with `[... N more items]`
  - Configurable sample size
- **CSV budget mode**: Emits column type inference, row count, null rates
- **Progressive deduction**: When `--max-tokens` is exceeded:
  1. Dedupe consecutive duplicate blocks
  2. Abbreviate long values
  3. Sample KeyValue arrays
  4. Summarize tables (header-only or compact CSV)

Output reports what was dropped at each stage.

## Format Support

### CSV/TSV
- Handles unknown encodings, BOMs, mixed quotes, ragged rows, duplicate headers
- Default: Markdown table
- `--table-style csv` for compact CSV-in-codeblock

### JSON/JSONL
- Flattens deeply nested structures
- Collapses repeated schemas into schema + rows form
- Truncates long arrays with `[... N more items]`
- Default: compact JSON

### DOCX
- Headings (1-6), lists, tables, footnotes, hyperlinks
- Tracked changes (accepted by default)
- Comments (optional flag)
- Default: Markdown with markdown tables

### PDF (optional, `ctxify[pdf]`)
- Text-based PDFs including tables, multi-column layouts, headers/footers
- Detects scanned/image-only PDFs and warns (OCR behind extra dependency)

### Stretch: XLSX, HTML, plain text/logs

## Output Formats

### Markdown (default)
Clean Markdown with appropriate headings, tables, and structure.

### JSON-Compact
One-line compact JSON representation.

### YAML
YAML representation of the content.

### Text
Plain text extraction.

## Front Matter

When `--front-matter=True`, emits YAML front matter like:

```yaml
---
source: report.docx
type: docx
pages: 5
token-estimate: 2400
warnings:
  - encoding-guessed
  - tracked-changes
```

## Development

```bash
# Install in dev mode
pip install -e ".[pdf,docx,ocr,xlsx]"

# Run tests
python -m pytestctxify

# Run ruff linter
ruff check .

# Format code
ruff format
```

## License

MIT License - see the LICENSE file for details.
