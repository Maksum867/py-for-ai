"""
cli.py module
=============
Command-line interface (CLI) for the Py-For-AI utility using the built-in argparse.
Provides user interaction, argument parsing, and triggers the packing logic.
"""

import argparse
import os
import re
import sys
from pathlib import Path

from py_ai import __version__
from py_ai.core import _format_bytes, pack_project
from py_ai.formatting import available_formats

_SIZE_SUFFIXES = {
    "B": 1,
    "KB": 1024,
    "MB": 1024 ** 2,
    "GB": 1024 ** 3,
}


def _scaled_number(value: str, multiplier: int) -> int:
    """Scale a decimal string exactly, rounding toward zero."""
    if len(value) > 100:
        raise ValueError("numeric value is too large")
    whole, _, fraction = value.partition(".")
    return int(whole) * multiplier + (int(fraction or "0") * multiplier // 10 ** len(fraction))


def parse_size(value: str) -> int:
    """
    Parses a human-friendly size like '512KB', '10MB' or '1048576'
    into a number of bytes. Raises ValueError on invalid input.
    """
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*(B|KB|MB|GB)?\s*", value.strip(), re.IGNORECASE)
    if not match:
        raise ValueError(f"invalid size value: '{value}' (examples: 512KB, 10MB, 1048576)")
    suffix = (match.group(2) or "B").upper()
    result = _scaled_number(match.group(1), _SIZE_SUFFIXES[suffix])
    if result <= 0:
        raise ValueError(f"size must be greater than zero: '{value}'")
    return result


def parse_token_budget(value: str) -> int:
    """
    Parses a human-friendly token budget like '128k', '1.5m' or '200000'
    into a number of tokens (k = 1,000, m = 1,000,000 — the way LLM context
    windows are usually quoted). Raises ValueError on invalid input.
    """
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([kKmM])?\s*", value.strip())
    if not match:
        raise ValueError(f"invalid token budget: '{value}' (examples: 128k, 1m, 200000)")
    suffix = (match.group(2) or "").lower()
    multiplier = {"": 1, "k": 1_000, "m": 1_000_000}[suffix]
    result = _scaled_number(match.group(1), multiplier)
    if result <= 0:
        raise ValueError(f"token budget must be greater than zero: '{value}'")
    return result


def _program_name() -> str:
    """Returns the program name to display, based on how the tool was invoked.

    The console scripts 'py-for-ai', 'pyai' and 'py-ai' share an entry point,
    and `python -m py_ai` goes through __main__.py, so the name must be
    derived from argv[0] instead of being hardcoded.
    """
    argv0 = os.path.basename(sys.argv[0]) if sys.argv and sys.argv[0] else ""
    if argv0.endswith("__main__.py"):
        return "python -m py_ai"
    if argv0.startswith("py-for-ai"):
        return "py-for-ai"
    if argv0.startswith("py-ai"):
        return "py-ai"
    if argv0.startswith("pyai"):
        return "pyai"
    return "pyai"

def _enable_unicode_output() -> None:
    """Prevent crashes when stdout/stderr are attached to pipes that use
    legacy code pages (Windows consoles use cp125x for redirected output).
    Unencodable glyphs (emoji) degrade to '?' instead of raising
    UnicodeEncodeError."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")  # type: ignore[union-attr]
        except (AttributeError, ValueError, OSError):
            pass  # non-standard streams (e.g. test capture, odd embedders)

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=_program_name(),
        description="Py-For-AI: pack your project's codebase into a single text (or Markdown) file "
                    "and copy it to the clipboard for LLM context."
    )

    parser.add_argument(
        "root_dir",
        nargs="?",
        default=".",
        help="Path to the project directory to pack (default: current directory '.')"
    )

    parser.add_argument(
        "-o", "--output",
        default="ai_context.txt",
        help="Name or path of the output file (default: 'ai_context.txt')"
    )

    parser.add_argument(
        "--no-clipboard",
        action="store_true",
        help="Disable automatic copying of the generated context to the system clipboard "
             "(useful for headless servers or CI/CD pipelines)."
    )

    parser.add_argument(
        "--format",
        choices=available_formats(),
        default="text",
        dest="output_format",
        help="Output format: 'text' (classic file markers) or 'markdown' "
             "(fenced code blocks with language detection). Default: text."
    )

    parser.add_argument(
        "--max-file-size",
        metavar="SIZE",
        default=None,
        help="Skip files larger than SIZE (e.g. 512KB, 10MB). Skipped files are "
             "listed with a warning and marked in the directory tree."
    )

    parser.add_argument(
        "--exclude",
        action="append",
        default=[],
        metavar="PATTERN",
        help="Glob pattern(s) to exclude additionally (e.g. '*.log', 'docs/*'). "
             "Can be passed multiple times."
    )

    parser.add_argument(
        "--include",
        action="append",
        default=[],
        metavar="PATTERN",
        help="Pack ONLY files matching this glob pattern (e.g. 'src/**', '*.py'). "
             "Can be passed multiple times; combine with --exclude for exceptions."
    )

    parser.add_argument(
        "--budget",
        default=None,
        metavar="TOKENS",
        help="Warn when the pack exceeds this LLM token budget (e.g. 128k, 1m, "
             "200000) and name the heaviest files to drop first."
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview only: show what would be packed (files by token weight, "
             "totals, budget verdict) without writing a file or copying."
    )

    parser.add_argument(
        "--strip-docs",
        action="store_true",
        help="Strip comments and docstrings from Python files to shrink the "
             "pack (note: the LLM also loses those explanations)."
    )

    parser.add_argument(
        "--no-type-summary",
        action="store_true",
        help="Omit the per-extension 'File types:' summary from the header."
    )

    parser.add_argument(
        "--no-gitignore",
        action="store_true",
        help="Do not honor .pyaiignore/.gitignore files even when the optional "
             "'pathspec' dependency (py-for-ai[gitignore]) is installed."
    )

    verbosity = parser.add_mutually_exclusive_group()
    verbosity.add_argument(
        "-q", "--quiet",
        action="store_true",
        help="Suppress informational output (scan banner, summary, clipboard "
             "status). Errors and warnings are still shown on stderr."
    )
    verbosity.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Print extra details in the summary (e.g. total source lines)."
    )

    parser.add_argument(
        "--no-tree",
        action="store_true",
        help="Omit the directory tree section from the output (both formats)."
    )

    parser.add_argument(
        "--no-token-count",
        action="store_true",
        help="Skip token estimation (faster on large projects); the header "
             "then reports 'Estimated tokens: disabled'."
    )

    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}"
    )

    return parser


def main():
    """
    Main entry point for the command-line interface. Parses CLI arguments,
    starts the project packaging process, and prints results to the console.
    """
    _enable_unicode_output()
    parser = _build_parser()
    args = parser.parse_args()

    root_path = Path(args.root_dir)
    output_path = Path(args.output)

    max_file_size = None
    if args.max_file_size is not None:
        try:
            max_file_size = parse_size(args.max_file_size)
        except ValueError as e:
            print(f"❌ Error: {e}", file=sys.stderr)
            sys.exit(2)

    budget = None
    if args.budget is not None:
        try:
            budget = parse_token_budget(args.budget)
        except ValueError as e:
            print(f"❌ Error: {e}", file=sys.stderr)
            sys.exit(2)

    if args.dry_run and args.no_clipboard is False:
        pass  # clipboard is skipped implicitly in dry-run mode

    if not args.quiet:
        try:
            resolved_root = root_path.resolve()
        except OSError:
            resolved_root = root_path.absolute()
        print(f"🔍 Scanning project directory: {resolved_root}")

    try:
        # Trigger the core packing logic
        stats = pack_project(
            root_dir=root_path,
            output_file=output_path,
            copy_to_clipboard=not args.no_clipboard,
            max_file_size=max_file_size,
            output_format=args.output_format,
            exclude_patterns=args.exclude,
            include_patterns=args.include,
            respect_gitignore=not args.no_gitignore,
            include_tree=not args.no_tree,
            enable_token_count=not args.no_token_count,
            strip_docs=args.strip_docs,
            include_type_summary=not args.no_type_summary,
            dry_run=args.dry_run,
            budget=budget,
        )

        if not args.dry_run and not args.no_clipboard and not stats["clipboard_copied"]:
            print(
                f"Warning: Could not copy to clipboard. "
                f"Details: {stats['clipboard_error'] or 'Unknown error'}. "
                "The output was still saved successfully to the output file.",
                file=sys.stderr,
            )

        if not args.quiet and args.dry_run:
            files = stats.get("files", [])
            ordered = sorted(files, key=lambda r: -(r["tokens"] or 0))
            print("\n🔍 DRY RUN — preview only (no file written, clipboard untouched)")
            print(f"{'Lines':>7}  {'~Tokens':>8}  File")
            for record in ordered[:20]:
                tokens = f"~{record['tokens']:,}" if record["tokens"] is not None else "-"
                print(f"{record['lines']:>7}  {tokens:>8}  {record['path']}")
            if len(ordered) > 20:
                print(f"  … and {len(ordered) - 20} more files")
            totals = (f"Total: {stats['packed_count']} files, "
                      f"{stats['total_lines']:,} lines")
            if stats["token_method"] != "disabled":
                totals += f", ~{stats['estimated_tokens']:,} tokens ({stats['token_method']})"
            print(totals)
            if budget is not None:
                if stats["budget_exceeded"]:
                    print(f"⚠️  Budget EXCEEDED: ~{stats['estimated_tokens']:,} > {budget:,} — "
                          f"drop the heaviest files listed above.")
                else:
                    print(f"✅ Within budget: ~{stats['estimated_tokens']:,} <= {budget:,}")
            sys.exit(0)

        if not args.quiet:
            print("\n✨ Project successfully packed!")
            print(f"📁 Root Directory:   {stats['root_dir']}")
            print(f"📄 Output File:      {stats['output_file']}")
            print(f"🧾 Output Format:    {stats['output_format']}")
            print(f"💾 Output Size:      {_format_bytes(stats.get('file_size', 0))}")
            print(f"📦 Packed Files:     {stats['packed_count']}")
            print(f"📏 Total Lines:      {stats['total_lines']}")
            if args.verbose:
                print(f"📄 Source Lines:     {stats['total_source_lines']}")
            if stats['token_method'] == "disabled":
                print("🪙 Estimated Tokens: disabled")
            else:
                print(f"🪙 Estimated Tokens: ~{stats['estimated_tokens']} ({stats['token_method']})")

            if budget is not None:
                if stats["budget_exceeded"]:
                    print(f"⚠️  Budget EXCEEDED: ~{stats['estimated_tokens']:,} > {budget:,}")
                else:
                    print(f"✅ Within budget: ~{stats['estimated_tokens']:,} <= {budget:,}")

            if stats['failed_count'] > 0:
                print(f"⚠️  Skipped Files:    {stats['failed_count']} (see warning messages above; they are marked '[skipped: ...]' in the directory tree)")

            if stats['clipboard_copied']:
                print("📋 Project context copied to clipboard successfully!")
            else:
                if args.no_clipboard:
                    print("ℹ️  Clipboard copying was disabled by the --no-clipboard flag.")

    except FileNotFoundError as e:
        print(f"❌ Error: {e}", file=sys.stderr)
        sys.exit(1)
    except OSError as e:
        print(f"❌ Write Error: {e}", file=sys.stderr)
        sys.exit(1)
    except ValueError as e:
        print(f"❌ Error: {e}", file=sys.stderr)
        sys.exit(2)
    except Exception as e:
        print(f"❌ Unexpected Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
