"""Command-line interface.

Exit codes: 0 success (warnings allowed), 1 ``--strict`` failure, 2 usage or I/O error.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from . import __version__
from .errors import ConversionError
from .ir import ConversionWarning
from .options import CSV_BUDGETS, FORMATS, TABLE_STYLES, TOKEN_BACKENDS, Options
from .pipeline import convert
from .tokens import TokenizerUnavailable

_EXT = {"md": ".md", "json-compact": ".json", "yaml": ".yaml", "text": ".txt"}
_TABLE_STYLE_CHOICES = (*TABLE_STYLES, "keyvalue")
_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}


def _bool(value: str) -> bool:
    lowered = value.strip().lower()
    if lowered in _TRUE:
        return True
    if lowered in _FALSE:
        return False
    raise ValueError(f"expected a boolean, got {value!r}")


def _normalize_bool_flags(argv: Sequence[str]) -> list[str]:
    """Support the documented ``--front-matter=True`` spelling without ambiguity."""
    flags = ("--front-matter", "--strict")
    out: list[str] = []
    for arg in argv:
        name, sep, value = arg.partition("=")
        if sep and name in flags:
            if _bool(value):
                out.append(name)
            continue
        out.append(arg)
    return out


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="ctxify",
        description="Convert messy files into clean, token-optimized context for LLMs.",
    )
    p.add_argument("inputs", nargs="*", metavar="INPUT", help="files to convert; '-' reads stdin")
    p.add_argument("-t", "--type", metavar="NAME", help="force a converter (see --list-types)")
    p.add_argument("-f", "--format", choices=FORMATS, default="md", help="output format")
    p.add_argument("-o", "--out", metavar="DIR", help="write one file per input into DIR")
    p.add_argument(
        "--front-matter", action="store_true", help="prepend source/type/tokens/warnings"
    )
    p.add_argument("--strict", action="store_true", help="fail instead of repairing or skipping")
    p.add_argument(
        "--token-backend", choices=TOKEN_BACKENDS, default="auto", help="token counter to use"
    )
    p.add_argument(
        "--max-tokens",
        type=int,
        metavar="N",
        help="hard token budget; apply progressive reduction until the body fits",
    )
    p.add_argument(
        "--table-style",
        choices=_TABLE_STYLE_CHOICES,
        default="auto",
        help="table representation (keyvalue is an alias for records)",
    )
    p.add_argument(
        "--budget",
        choices=CSV_BUDGETS,
        default="full",
        help="full rows, or column statistics plus a sample",
    )
    p.add_argument("-q", "--quiet", action="store_true", help="suppress warnings on stderr")
    p.add_argument("-v", "--verbose", action="store_true", help="also show info-level warnings")
    p.add_argument("--list-types", action="store_true", help="list converters and exit")
    p.add_argument("--version", action="version", version=f"ctxify {__version__}")
    return p


def _report(warnings: Sequence[ConversionWarning], label: str, verbose: bool) -> None:
    for w in warnings:
        if w.severity == "info" and not verbose:
            continue
        where = f" ({w.where})" if w.where else ""
        print(f"ctxify: {label}: {w.severity}[{w.code}] {w.message}{where}", file=sys.stderr)


def _write_stdout(text: str) -> None:
    # errors="replace" is a last-resort guard: a lone surrogate must never crash the CLI.
    data = text.encode("utf-8", "replace")
    buffer = getattr(sys.stdout, "buffer", None)
    if buffer is not None:
        buffer.write(data)
        buffer.flush()
    else:
        sys.stdout.write(text)


def _write_file(text: str, item: str, out_dir: Path, fmt: str, used: set[str]) -> Path:
    # Path(...).name drops any directory components, so inputs cannot escape out_dir.
    base = "stdin" if item == "-" else (Path(item).name or "output")
    ext = _EXT[fmt]
    name, n = f"{base}{ext}", 2
    while name in used:  # a.pdf and dir2/a.pdf would otherwise overwrite each other
        name, n = f"{base}-{n}{ext}", n + 1
    used.add(name)
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / name
    target.write_text(text, encoding="utf-8", errors="replace", newline="\n")
    return target


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point. Returns the process exit code."""
    parser = _parser()
    raw = list(argv) if argv is not None else sys.argv[1:]
    try:
        args = parser.parse_args(_normalize_bool_flags(raw))
    except ValueError as exc:
        parser.error(str(exc))

    if args.list_types:
        from . import registry

        missing = registry.missing()
        for conv in registry.converters():
            print(f"{conv.name}\t{' '.join(conv.extensions)}")
        for name, reason in sorted(missing.items()):
            print(f"!{name}\tunavailable: {reason}", file=sys.stderr)
        return 0
    if not args.inputs:
        parser.error("no input given (use '-' to read stdin)")
    if len(args.inputs) > 1 and not args.out:
        parser.error("multiple inputs require --out DIR")
    if args.inputs.count("-") > 1:
        parser.error("'-' (stdin) can only be given once")

    try:
        opts = Options(
            type=args.type,
            format=args.format,
            front_matter=args.front_matter,
            strict=args.strict,
            token_backend=args.token_backend,
            max_tokens=args.max_tokens,
            table_style="records" if args.table_style == "keyvalue" else args.table_style,
            budget=args.budget,
        )
    except ValueError as exc:
        parser.error(str(exc))

    code = 0
    used: set[str] = set()
    try:
        for item in args.inputs:
            try:
                result = convert(sys.stdin.buffer if item == "-" else item, options=opts)
            except ConversionError as exc:
                print(f"ctxify: {item}: {exc}", file=sys.stderr)
                code = max(code, 1)
                continue
            except TokenizerUnavailable as exc:
                print(f"ctxify: error: {exc}", file=sys.stderr)
                return 2
            except (OSError, ValueError) as exc:
                print(f"ctxify: error: {item}: {exc}", file=sys.stderr)
                code = max(code, 2)
                continue
            if not args.quiet:
                _report(result.warnings, item, args.verbose)
            try:
                if args.out:
                    target = _write_file(result.text, item, Path(args.out), args.format, used)
                    if not args.quiet:
                        print(
                            f"ctxify: wrote {target} ({result.stats.tokens} tokens)",
                            file=sys.stderr,
                        )
                else:
                    _write_stdout(result.text)
            except BrokenPipeError:
                return code  # e.g. `ctxify x | head`
            except OSError as exc:
                print(f"ctxify: error: cannot write output for {item}: {exc}", file=sys.stderr)
                code = max(code, 2)
    except BrokenPipeError:
        return code
    return code
