# Changelog

## [0.5.0] - 2026-11-08

### Added
- `py-for-ai` console command; `pyai` and `py-ai` remain compatible aliases.
- PyPI Trusted Publishing workflow triggered by published GitHub releases, with description, version, verification and build checks.
- Static type checking in CI and development dependencies.

### Changed
- Integrated audit regression tests into the repository's test suite.
- Removed the accidentally committed verification script.

### Fixed
- Show clipboard failures on stderr even in quiet mode; include bare root directory names.
- Preserve generator-based exclude patterns across all scanned entries.
- Match wildcard directory patterns ending in `/` or `/**` in `--include`.
- Apply built-in filtering to symlink names as well as targets; do not follow
  symlinked ignore files.
- Preserve escaped whitespace and literal directory names in nested ignore rules.
- Write output atomically, avoiding truncation on failure and mutation of hardlinks.
- Reject NUL-containing BOM-decoded files; use stable case-sensitive tie-breaking.
- Parse large decimal sizes and budgets exactly and validate API size limits.
- Keep Python 3.8 development installation compatible with available type stubs.
- Update repository links and publishing instructions after the GitHub rename.
- Prevent file symlinks from bypassing exclusions on their targets.
- Deduplicate multiple symlinks to the same file when the real file is not selected.
- Support include patterns with wildcards in directory components.
- Preserve code and string literals in the Python 3.8 docstring-stripping fallback.
- Reject unreasonably large numeric CLI arguments with a clean error.
- Correct public outside-root filtering for paths with symlinked parents and internal type annotations.

## [0.4.0] - 2026-09-13

### Added
- **`--include PATTERN`** (repeatable): pack ONLY files matching the pattern —
  e.g. `pyai --include 'src/**'`, `--include '*.py'`. Directories that cannot
  contain matching files are pruned from traversal and from the tree, so the
  tree stays consistent with the packed content. Works together with
  `--exclude` (exclude wins).
- **Per-file statistics + `--budget TOKENS`**: every pack reports per-file
  lines/tokens (see the returned stats and `--format json`), and `--budget`
  (e.g. `--budget 128k`) warns when the pack exceeds an LLM context limit and
  names the heaviest files to drop first. `128k` means 128,000 tokens.
- **`--dry-run`**: preview mode — prints what would be packed (files sorted by
  token weight, totals, budget verdict) without writing any file or touching
  the clipboard.
- **`--format json`**: machine-readable output — one JSON document with
  metadata, statistics, per-file entries (`path`, `lines`, `tokens`,
  `content`), skipped files and the directory tree; ideal for scripts and
  per-file API processing.
- **File-type summary in the header**: every text/markdown pack now ends its
  header with a compact per-extension breakdown
  (e.g. `File types: .py ×12 (3,456 lines), .md ×2 (120 lines)`).
  Disable with `--no-type-summary`.
- **`--strip-docs`**: strips comments and docstrings from Python files
  (AST-based, syntax-error files pass through untouched) to shrink the pack —
  at the cost of losing the explanations an LLM could lean on, so it is
  opt-in.

## [0.3.1] - 2026-09-13

### Fixed
- **RecursionError on deeply nested projects on Python <= 3.11** (`core.py`,
  `filters.py` — caught by the new CI matrix, which is exactly what it exists
  for): `Path.resolve()` (recursive `realpath` before Python 3.12) and
  `os.walk` (recursive before Python 3.12) both crashed on ~1100-level trees.
  Path comparison for non-symlink entries now uses iterative `abspath`, and
  ignore-file discovery uses an explicit stack. Python 3.8-3.14 verified.
- **CRLF line endings leaked into the pack on Windows** (`readers.py`): source
  files created on Windows carry `\r\n`, which was packed as-is — extra CR
  noise (and tokens) for the LLM and platform-dependent statistics. Content is
  now normalized to LF at read time, so a pack is byte-identical regardless of
  the OS it was produced on.
- **Nested `.gitignore` files lost git's "any depth" semantics** (`filters.py`):
  an unanchored pattern (e.g. `*.tmp`) in `sub/.gitignore` must ignore matching
  files at ANY depth below `sub/`, but rebasing it to `sub/*.tmp` anchored it to
  the direct children only — deeper files silently leaked into the pack. Nested
  unanchored patterns are rebased to `sub/**/<pattern>` now; anchored patterns
  (with an inner slash) keep `sub/<pattern>`. Verified against real git
  semantics with `pathspec`.
- **"Estimated tokens" was systematically underreported** (`core.py`): tokens
  were counted on an intermediate document and the header was re-assembled once
  afterwards, so the final file contained a stale (lower) number. The assembly
  is now repeated to a fixed point — the reported lines/tokens describe the
  final file exactly (verified for both tiktoken and the heuristic).
- **Phantom CI**: README and the 0.3.0 changelog claimed
  `.github/workflows/tests.yml` exists ("3 OS x 6 Python versions"), but the
  file was never committed. The workflow is actually present now (pytest + ruff
  on ubuntu/windows/macos x Python 3.8-3.13).
- **Dangling symlinks vanished silently** (`core.py`): a symlink whose target
  does not exist is now shown in the tree with a
  `[dangling symlink — target missing]` note.
- **File symlinks inside the project were packed twice** (`core.py`): an alias
  like `alias.py -> real.py` duplicated the content in the pack. Aliases of
  already-collected files are skipped now (identity via inode, resolved-path
  fallback) and stay visible in the tree with a
  `[symlink alias — content already packed ...]` note.
- **Binary files with a NUL tail were packed as "text"** (`readers.py`): the
  NUL-byte heuristic inspected only the first 8 KiB, but a NUL is valid UTF-8,
  so such files produced text with embedded NULs. The whole buffer is scanned
  now (`check_bytes` stays available for git-like windowed checks).
- **`.svg` was filtered out as binary** (`filters.py`): SVG is a text/XML
  format that is often useful as LLM context; it is packed now (highlighted as
  `xml` in Markdown output) and true binary content is still caught by the
  NUL heuristic.
- **Empty directories stayed in the tree after exclusion** (`core.py`): e.g.
  `--exclude 'docs/*'` removed all files but left the empty `docs/` folder.
  Directories whose entire subtree was filtered out are pruned from the tree
  now (memoized bottom-up visibility check — O(n) per run).
- **`python -m py_ai --help` displayed the wrong program name** (`cli.py`):
  module invocation now shows `python -m py_ai` instead of `pyai`.
- **Performance on deeply nested projects** (`core.py`, `filters.py`): a
  1100-level chain packs ~2.8x faster (resolve() calls skipped for the common
  non-symlink case); the pruning visibility check is memoized and adds no
  asymptotic cost.

### Added
- **`py.typed`** marker (`src/py_ai/`): the package is fully type-annotated and
  now ships PEP 561 type information to type checkers.
- **Full PyPI classifiers** (`pyproject.toml`): Python 3.8-3.13 version
  classifiers, Development Status, Environment, Intended Audience + keywords,
  so the PyPI "Python Support" badge reflects reality.
- **Language detection by file name** (`formatting.py`): `Dockerfile`,
  `Makefile`, `CMakeLists.txt`, `.gitignore`, `.editorconfig` etc. now get
  proper Markdown fence languages.
- **Python 3.14** added to classifiers and the CI matrix (3 OS × 7 versions).
- **21 new regression tests**: nested-gitignore depth/anchoring/negation,
  symlink alias dedup, dangling-link note, token-stat fixed point, tree
  pruning, NUL-tail detection, newline normalization, language detection,
  program name. The deep-nesting test now cleans up after itself
  iteratively (pytest's own recursive tmp-dir garbage collection cannot
  crash on Python <= 3.11) and caps its depth on macOS, whose 1024-byte
  PATH_MAX makes an 1100-level tree physically impossible.
- **CI failures are now debuggable**: when pytest fails, the last 120 lines
  of its output are written to the workflow run Summary, which is viewable
  without repository permissions.

### Notes (documented behaviour, unchanged by design)
- UTF-16/32 files WITHOUT a BOM contain NUL bytes and are skipped as binary.
- The final `latin-1` fallback never fails, so exotic legacy encodings may be
  transcoded with mojibake instead of being skipped.
- Directories named `env`/`venv`/`build`/`dist` are ignored at ANY depth
  (common Python/gitignore convention).
- In the classic text format, a source file containing its own
  `--- START OF FILE: ... ---` lines can confuse naive parsers of the pack;
  the Markdown format is immune thanks to adaptive fences.


## [0.3.0] - 2026-08-01

### Added
- **`--quiet` / `-q` and `--verbose` / `-v`** (`cli.py`): suppress all
  informational output (scan banner, summary, clipboard status) for CI, or
  print extra details (e.g. total source lines). Errors and warnings still go
  to stderr. The flags are mutually exclusive.
- **`--no-tree`** (`cli.py`, `core.py`, `formatting.py`): omit the directory
  tree section from both output formats.
- **`--no-token-count`** (`cli.py`, `core.py`): skip token estimation for
  large projects; the header then shows `Estimated tokens: disabled`.
- **Transcoding transparency** (`readers.py`, `core.py`): the reader now
  reports which codec was actually used, and the packer prints a note when a
  non-UTF-8 file (cp1251, latin-1, UTF-16/32) is transcoded to UTF-8.

### Fixed
- **`--exclude 'dir/'` also matched a same-named regular file** (`filters.py`):
  a git-style directory pattern now matches only the directory itself (or
  anything under it), not a file literally named like the pattern.
- **`ai_context.txt` exclusion was applied at any depth** (`core.py`): only a
  root-level leftover pack with the default output name is excluded now; a
  legitimate nested file named `ai_context.txt` stays in the pack.
- **Stale type annotations** for the visited-directories set (`core.py`):
  they describe the actual `_dir_identity()` keys.
- Docs: removed the brittle hardcoded test count from the README.
- Added a one-file pre-release verification script (tests, lint, CLI smoke,
  git hygiene, build, wheel smoke).
- **Ruff lint fixes** (`src/`, `tests/`): sorted imports/`__all__`, removed an
  unused import, `IOError` → `OSError` (alias in Python 3), dropped a
  redundant UTF-8 argument; added a `[tool.ruff]` config documenting the
  intentionally-ignored rules (`BLE001`, `S110`, `DTZ005`).
- **Silent data loss on filesystems with unreliable inodes** (`core.py`): duplicate/cycle
  detection keyed only on `(st_dev, st_ino)` treated every directory as a duplicate when
  `st_ino` was 0 or repeated (some network/FUSE mounts), silently dropping whole subtrees.
  A stable directory identity (inode when available, resolved path otherwise) is used now.
- **`.pyaiignore` / `.gitignore` interplay** (`filters.py`): both files were compiled into
  separate `PathSpec` objects, so `!` negation never worked across files and `.pyaiignore`
  could not override `.gitignore`. All ignore files are now merged into a single matcher
  (later rules win), so `.pyaiignore` correctly takes precedence.
- **Nested `.gitignore` files were ignored** (`filters.py`): only root-level ignore files
  were read. All ignore files inside the project are now discovered and their patterns are
  rebased relative to the project root (git semantics).
- **`Output Size` reported characters instead of bytes** (`core.py`): `len()` understated
  non-ASCII content (e.g. Cyrillic in UTF-8). The on-disk byte size is reported now.
- **Legitimate files named like ignored directories were dropped** (`filters.py`): files
  literally named `dist`, `env`, `build`, `venv`, ... (no extension) were silently excluded
  because directory-oriented `IGNORED_NAMES` was applied to file names too. Only
  file-specific junk names (`.DS_Store`, `Thumbs.db`, `desktop.ini`, `.env`) filter files now.
- **`--exclude 'dir/'` did not work** (`filters.py`): a trailing-slash pattern now excludes
  the directory and everything under it (git-style); the `*`-crosses-`/` fnmatch behaviour is
  documented in the README.
- **Leftover default output packs were re-packed** (`core.py`): `ai_context.txt` from a
  previous run is never included in a new pack, even when the new output uses another name.
- **Misleading tree note for plain symlink aliases** (`core.py`): a normal `alias -> real`
  directory link is now annotated as "cyclic or duplicate link — already traversed, not
  followed" instead of "skipped".
- **`py-ai --version` printed `pyai 0.2.0`** (`cli.py`): the program name is now derived
  from how the tool was invoked.
- **`--max-file-size 0` (or sub-byte sizes) was accepted** (`cli.py`): values `<= 0` are
  rejected with a clear error instead of silently skipping every file.
- **Potential unhandled `OSError` in the CLI banner** (`cli.py`): `Path.resolve()` is now
  guarded.
- **Committed junk removed from the repository**: `.idea/` and `src/py_ai/__pycache__/*.pyc`
  are no longer tracked; `.gitignore` covers them (and more).
- **Missing CI file**: the promised `.github/workflows/tests.yml` (3 OS × Python 3.8–3.14)
  is now present.
- **`verify_release.ps1` hardcoded the version**: it now reads it from `pyproject.toml`.
- Docs: test count and supported Python versions updated.

## [0.2.0] - 2026-07-31

### Added
- **Token estimation** (`py_ai/tokens.py`): every pack reports the total line count and an estimated token count in the output header and CLI summary. Uses `tiktoken` (`cl100k_base`) when installed (`pip install py-for-ai[tokens]`), otherwise a ~4 chars/token heuristic (the CLI always says which method was used).
- **Markdown output format** (`--format markdown`, `py_ai/formatting.py`): `#` statistics header, fenced ASCII tree, per-file fenced code blocks with language detection by extension and adaptive backtick fences (contents containing ``` are escaped safely).
- **`--max-file-size SIZE`** (e.g. `512KB`, `10MB`): oversized files are skipped with a warning and marked `[skipped: exceeds size limit (...)]` in the tree.
- **`--exclude PATTERN`** (repeatable): extra glob exclusions matched against the relative POSIX path and the file name.
- **`.pyaiignore` / `.gitignore` support** (`py_ai/filters.py`): honored when the optional dependency `pathspec` is installed (`pip install py-for-ai[gitignore]`); disable with `--no-gitignore`. A hint is printed when the files exist but the dependency is missing.
- **`--version`** flag.
- Optional dependency extras in `pyproject.toml`: `tokens`, `gitignore`, `all`, `dev`.
- **Full pytest suite** (55 tests) under `tests/` covering filtering, readers/encodings, symlink safety, output ordering/formatting, CLI exit codes and edge cases (1100-deep nesting, unreadable directories).
- **CI** (`.github/workflows/tests.yml`): pytest matrix on Linux/Windows/macOS × Python 3.8–3.13.
- CLI summary now also prints the output format and total line count.

### Fixed
- **Content ordering regression** (introduced during the 0.1.2 recursion fix): files within a directory were collected in reverse order and before their sibling subtrees. The walker now uses a single mixed work stack reproducing the exact classic DFS order (directories-with-subtrees first, then files, all sorted), and the ordering is locked by a test.
- **Aliased packing via directory symlinks**: a directory symlink (`alias -> real`) caused files to be packed under the alias path. Directory symlinks are now never traversed; they are rendered in the tree as `[directory symlink — not followed]` or, when their target was already visited, as a cycle/duplicate note.
- Markdown blocks no longer render a blank line before the closing fence when the file ends with a newline.
- Removed deprecation warnings with `pathspec` >= 0.12 (new 'gitignore' pattern factory with a legacy fallback).
- **Windows line endings**: the output file was written in text mode, so on
  Windows every `\n` was translated to `\r\n` and CRLF-sourced content was
  doubled to `\r\r\n` (visible as blank lines between all lines). The output
  is now written with `newline=""` (byte-exact). Found by the test suite on
  Windows / Python 3.14.
- Added CI coverage for the newest Python 3.14.

### Changed
- Codebase reorganized into focused modules: `filters.py` (ignore rules), `readers.py` (encodings), `tokens.py` (statistics), `formatting.py` (output assembly); `core.py` keeps orchestration and re-exports the previously public names (`should_ignore`, `read_text_content`, `IGNORED_NAMES`, ...) for backwards compatibility.

## [0.1.2] - 2026-07-31

### Fixed
- **Python 3.8/3.9 support**: the package declared `requires-python = ">=3.8"` but crashed on import with `TypeError: unsupported operand type(s) for |` (PEP 604 `str | Path` annotations were evaluated at import time). Added `from __future__ import annotations` so the declared `>=3.8` support now actually works.
- **Security**: symlinks resolving outside of the project root (e.g. pointing to `/etc` or `$HOME`) were silently packed into the output. Such entries are now excluded from the packed content and shown in the directory tree with an explicit warning note.
- **Symlink cycles**: a cyclic symlink (e.g. `sub/loop -> ../sub`) inflated the directory tree with ~40 nested duplicate levels (until the OS hit ELOOP). Directory traversal now tracks visited `(device, inode)` pairs and traverses cycles/duplicates only once.
- **RecursionError** on deeply nested projects (>~1000 levels): `maximum recursion depth exceeded` aborted the run with exit code 1. Both the directory walker and the tree builder are now iterative (explicit stack).
- **Inconsistent output**: the output file itself (e.g. `ai_context.txt`) was listed in the DIRECTORY TREE on repeated runs while being excluded from the content. It is now excluded from both. Files that fail to be read are kept visible in the tree with a `[skipped: ...]` note.
- **Incomplete binary filtering**: `.exe`, `.bin`, `.dat`, `.msi`, `.jar` and ML artifacts (`.pkl`, `.onnx`, etc.) were missing from the extension list and only caught by the decode-error fallback. The extension list is extended and a NUL-byte content heuristic was added.
- **`*.egg-info` directories** (e.g. `py_for_ai.egg-info/`) were packed even though `.egg-info` was listed in ignored names (real directories never match that exact name). Any path component ending in `.egg-info` is now ignored.
- **Non-UTF-8 text files** (e.g. legacy Windows-1250/1251 Cyrillic files, UTF-16 with BOM) were dropped from the pack. Added BOM-aware decoding and a fallback chain (UTF-8 → cp1251 → latin-1).
- **Duplicate clipboard warning** was printed twice (once from `core`, once from the CLI). The warning is now rendered exactly once by the CLI; `core` only reports it in the returned statistics.
- **Version mismatch**: `py_ai.__version__` reported `0.1.0` while the distribution was `0.1.1`. The version is now read from the installed distribution metadata (`importlib.metadata`) with a sensible fallback.
- **Double tree entry** for unreadable directories (the directory plus an extra sibling line with the error). The access error is now rendered as a single child note of the directory.
- **Docs**: wrong installation command (`pip install py-ai` — no such distribution), broken shield badges (`py-ai-pack`), undocumented `py-ai` alias and undocumented hidden-files filtering policy.

### Added
- CLI summary now prints the generated output size (`💾 Output Size`).
- `CHANGELOG.md`.

### Changed
- The hidden-files allowlist now also includes `.gitattributes`, `.gitmodules`, `.flake8`, `.coveragerc`, `.dockerignore`, `.editorconfig`, `.pre-commit-config.yaml`, `.python-version`, `.readthedocs.yaml`, `.readthedocs.yml`, `.codecov.yml` (matched case-insensitively).

## [0.1.1] - 2026-07-30
- Patch release.

## [0.1.0] - 2026-07-30
- Initial release.
