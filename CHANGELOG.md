# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-10-25

First working release. The previous `main` could not be imported, so this release
replaces the scaffold rather than extending it.

### Added

- Working conversion pipeline with detection, IR optimization, rendering, token
  counting and front matter.
- `--max-tokens` with progressive reduction (dedupe, abbreviation, sampling, compact
  CSV, header-only tables, trailing-block removal, fence-safe truncation) and
  `stats.dropped` reporting.
- `--table-style auto|markdown|csv|records` (`keyvalue` alias) and `--budget
  full|summary` for CSV/JSON.
- JSON/JSONL support, including per-line recovery, duplicate-key detection and
  non-finite number handling.
- DOCX support with document order, inline hyperlinks, real tracked-change detection,
  footnotes from `word/footnotes.xml`, and ZIP-bomb/XXE guards.
- Front-matter output that is valid YAML for every output format, including a
  `token_backend` field so cached counts are attributable.
- `--list-types`, `--version`, `--quiet`, `--verbose`, `--strict`, `--out` and
  explicit boolean values (`--front-matter=True`).
- Type information (`py.typed`) and a public library API: `convert`, `Options`,
  `Result`, `Stats`, `ConversionError`, `ConversionWarning`.
- CI (ruff, mypy, pytest on Python 3.10-3.14), LICENSE, `.gitattributes`, packaging
  metadata and an sdist that includes the test suite.

### Fixed

- `import ctxify` failed with `NameError`; the pipeline also contained a syntax error
  and an unfinished optimizer.
- The JSON converter was never registered, so JSON files were treated as text.
- A missing optional dependency (python-docx) broke every converter. Built-ins now
  load lazily and explain the extra to install.
- CSV read only the first 64 KiB, ignored delimiter detection (TSV parsed as one
  column), and silently removed duplicate and numeric-looking rows.
- JSON read only the first 64 KiB; JSONL used an array parser and lost truncated
  input; duplicate keys were silently collapsed and `NaN` produced invalid JSON.
- DOCX used an undefined `re` import, emitted tables before all paragraphs, reported
  every run as a tracked change, and decoded the ZIP container as text.
- YAML emitted exponent floats (`1e-05`) that loaded back as strings and did not
  escape U+2028/U+2029, DEL or other control characters.
- stdin pipes that report `seekable()` but lose data on `seek()` produced empty
  output.
- Lone UTF-16 surrogates from JSON escapes crashed UTF-8 output.
- The `docx` extra omitted `python-docx`; `pdf`/`ocr` extras were documented with no
  implementation; the wheel failed PEP 639 validation.

### Changed

- Relicensed under GPL-3.0-only. The GPLv3 `LICENSE` added on the remote replaces the
  earlier MIT text; `pyproject.toml`, the README and the release artifacts were updated
  to match.
- Strict mode raises `ConversionError` with every warning attached.
- Front matter uses `tokens` and `token_backend` (previously documented as a single
  `token-estimate`).
- `--budget summary` emits column type inference, row count, null rate and a sample.
- Token counting falls back to `chars/4` when tiktoken is unavailable and always
  reports which backend was used.

### Known limitations

- PDF, XLSX and HTML converters are not implemented; the corresponding extras were
  removed.
- DOCX comments are not extracted.
- `--budget summary` samples arrays/tables; use `full` (default) for every row.
