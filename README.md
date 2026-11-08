<div align="center">

# Py-For-AI 🚀

[![Python 3.8+](https://img.shields.io/badge/Python-3.8%2B-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://www.python.org/downloads/)
[![License MIT](https://img.shields.io/badge/License-MIT-E6C200?style=for-the-badge)](https://github.com/Maksum867/py-for-ai/blob/main/LICENSE)
[![PyPI version](https://img.shields.io/pypi/v/py-for-ai?style=for-the-badge&label=PyPI&color=FF6666)](https://pypi.org/project/py-for-ai/)

**Py-For-AI** is a lightweight, zero-configuration command-line interface (CLI) tool designed for developers. It recursively scans your local Python codebase, filters out unnecessary files, and compiles your entire project—complete with a beautiful ASCII directory tree and LLM-ready statistics (lines & estimated tokens)—into a single text or Markdown file while automatically copying it to your clipboard.

Perfect for instantly feeding your codebase context into Large Language Models (LLMs) like ChatGPT, Claude, and Gemini.

</div>

---

## ✨ Features

- **LLM-ready statistics**: every pack ends with the total line count and an **estimated token count** (accurate `cl100k_base` counting when `tiktoken` is installed, ~4 chars/token heuristic otherwise), printed in the output header and in the CLI summary.
- **Three output formats**: classic plain text (`--- START OF FILE: ... ---`), **Markdown** (`--format markdown`) with language-aware fenced code blocks, and **JSON** (`--format json`) with per-file metadata (`path`, `lines`, `tokens`, `content`) for scripts and API processing.
- **`--include` filter**: pack ONLY files matching a pattern (`pyai --include 'src/**'`, `--include 'src'`, `--include '*.py'`); directories that cannot contain matching files are pruned from the tree, and it composes with `--exclude`.
- **LLM budget control (`--budget`)**: warn when the pack exceeds an LLM context limit (e.g. `--budget 128k`) and name the heaviest files to drop first; per-file line/token statistics are always available.
- **`--dry-run` preview**: see exactly what would be packed (files sorted by token weight, totals, budget verdict) without writing anything.
- **`--strip-docs`**: optionally strip comments and docstrings from Python files (AST-based, syntax-error files pass through untouched) to shrink the pack.
- **File-type summary**: every pack header ends with a compact per-extension breakdown (e.g. `File types: .py x12 (3,456 lines), .md x2 (120 lines)`); disable with `--no-type-summary`.
- **Smart Filtering**: automatically ignores VCS folders (`.git`, `.github`), virtual environments (`.venv`, `venv`), IDE settings (`.vscode`, `.idea`), build artifacts (`dist`, `build`, `*.egg-info`), caches (`__pycache__`, `.pytest_cache`), binary files (images, archives, databases, executables — plus a NUL-byte content heuristic) and hidden files (except explicitly allowed configs like `.gitignore`, `.env.example`, `.editorconfig`, `.dockerignore`).
- **`.gitignore` / `.pyaiignore` support**: honored automatically when the optional `pathspec` dependency is installed (`pip install py-for-ai[gitignore]`).
- **Custom exclusions**: additional glob patterns via `--exclude` (repeatable) and a per-file size cap via `--max-file-size`.
- **Output control**: `--quiet`/`-q` for CI-friendly silent runs (errors still go to stderr), `--verbose`/`-v` for extra details, `--no-tree` to drop the directory tree section, and `--no-token-count` to skip token estimation on large projects.
- **Symlink-Safe**: symlinks pointing **outside** the project root are never followed or packed; directory symlinks are never traversed, so cycles and aliased duplicates are impossible; file-symlink aliases of already-packed files are packed only once. Suspicious links (outside-root, dangling, aliased) stay visible in the directory tree with an explanatory note.
- **Deep-Project-Proof**: iterative (stack-based) directory traversal — no `RecursionError` on very deeply nested projects.
- **Encoding-Aware**: reads UTF-8, UTF-8-BOM, UTF-16/32 (via BOM), legacy Cyrillic `cp1251` and other 8-bit encodings automatically; line endings are normalized to LF (CRLF/CR → LF), so packs are byte-consistent across platforms; true binary files (NUL-byte heuristic over the whole file) are skipped with a clear warning and marked in the tree.
- **ASCII Directory Tree**: a clean, sorted representation of your project layout, consistent with the packed content (skipped files are annotated).
- **Clipboard Integration**: automatically copies the packed content; gracefully falls back with a warning in headless/SSH environments.

---

## ⚠️ Known limitations (by design)

- **UTF-16/32 without a BOM** contains NUL bytes and is skipped as binary — such files cannot be reliably told apart from real binary data.
- The final **`latin-1` fallback never fails**, so an exotic legacy encoding may be transcoded with mojibake instead of being skipped.
- Directories named `env`, `venv`, `build`, `dist` are ignored **at any depth** (common Python/`.gitignore` convention). A legitimate data folder with one of these names will not be packed.
- In the classic **text format**, a source file that itself contains `--- START OF FILE: ... ---` lines can confuse naive parsers of the pack. The Markdown format is immune thanks to adaptive fences.
- Token estimation with the `~4 chars/token` heuristic is approximate; install `py-for-ai[tokens]` for exact `cl100k_base` counting (both computed over the final document).

## 📂 Project Layout

```text
py_ai_project/
├── pyproject.toml
├── README.md
├── CHANGELOG.md
├── .github/workflows/            # CI checks and PyPI publishing
├── tests/                        # pytest suite
└── src/py_ai/
    ├── __init__.py               # version (read from installed metadata)
    ├── __main__.py               # python -m py_ai
    ├── cli.py                    # argument parsing & user interaction
    ├── core.py                   # traversal orchestration + tree builder
    ├── filters.py                # ignore rules, --exclude, .gitignore support
    ├── readers.py                # encoding/binary-aware file reading
    ├── stripping.py              # --strip-docs (AST comment/docstring removal)
    ├── tokens.py                 # line & token statistics
    ├── formatting.py             # text / markdown output assembly
    └── py.typed                  # PEP 561 type marker
```

---

## 🚀 Quick Start

### Installation

Install the utility directly from PyPI (note: the **distribution** name is `py-for-ai`):

```bash
pip install py-for-ai
```

Optional extras:

```bash
pip install "py-for-ai[tokens]"       # accurate token counting via tiktoken
pip install "py-for-ai[gitignore]"    # respect .gitignore / .pyaiignore files
pip install "py-for-ai[all]"          # everything above
```

*(Or install it locally for development)*:

```bash
git clone https://github.com/Maksum867/py-for-ai.git
cd py-for-ai
pip install -e ".[dev]"
pytest
```

### Usage

Run the utility with `py-for-ai`, `pyai`, or the backwards-compatible `py-ai` alias. The Python module remains `py_ai`:

```bash
# 1. Pack current directory and copy to clipboard:
pyai

# 2. Pack a specific directory:
pyai /path/to/your/project

# 3. Save the results to a custom file:
pyai . -o custom_output.txt

# 4. Run without clipboard copy (ideal for remote servers or CI/CD):
pyai --no-clipboard

# 5. Markdown output with syntax-aware code fences:
pyai --format markdown -o context.md

# 6. Skip large files and extra paths:
pyai --max-file-size 200KB --exclude '*.log' --exclude 'docs/*'

# 7. Same via the project-name command or as a module; show the installed version:
py-for-ai --no-clipboard
python -m py_ai --version

# 8. CI-friendly: no informational output, no clipboard, no tree:
pyai --quiet --no-clipboard --no-tree -o context.txt

# 9. Skip token estimation (faster on big repos) or show extra details:
pyai --no-token-count
pyai --verbose

# 10. Pack ONLY the source tree (include filter), preview first:
pyai --dry-run --include 'src/**'
pyai --include 'src/**' --include '*.md' -o context.txt

# 11. Check the pack against an LLM context budget:
pyai --dry-run --budget 128k
pyai --budget 128k -o context.txt

# 12. Machine-readable output for scripts / per-file API calls:
pyai --format json -o context.json

# 13. Shrink the pack by stripping Python comments and docstrings:
pyai --strip-docs -o lean_context.txt
```

---

## 📄 Output Format Examples

### Plain text (default)

```text
================================================================================
PROJECT CONTEXT PACK: my_project
Generated on: 2026-07-31 12:00:00
Total files packed: 2
Total lines: 33
Estimated tokens: ~410 (heuristic (~4 chars/token))
================================================================================

================================================================================
DIRECTORY TREE
================================================================================
my_project/
├── src/
│   └── main.py
└── pyproject.toml

================================================================================
FILES CONTENT
================================================================================

--- START OF FILE: pyproject.toml ---
[project]
name = "my_project"
version = "0.1.0"
--- END OF FILE: pyproject.toml ---

--- START OF FILE: src/main.py ---
def main():
    print("Hello, AI!")
--- END OF FILE: src/main.py ---
```

### Markdown (`--format markdown`)

````markdown
# Project Context Pack
- Project: my_project
- Generated on: 2026-07-31 12:00:00
- Total files packed: 1
- Total lines: 15
- Estimated tokens: ~120 (cl100k_base (tiktoken))

## Directory Tree

```text
my_project/
└── src/
    └── main.py
```

## Files Content

### `src/main.py`
```python
def main():
    print("Hello, AI!")
```
````

Files skipped during reading (for example NUL-containing or oversized files) and unsafe symlinks are shown in the tree with an explanatory note. Files filtered out by name or extension are omitted from the tree. For example:

```text
├── data/
│   ├── dump.txt  [skipped: binary file]
│   ├── huge.log  [skipped: exceeds size limit (200.0 KB)]
│   └── notes.txt -> /etc/hostname  [symlink outside project root — not followed, not packed]
```

---

## ⚙️ Filtering Behavior (what exactly is excluded)

- **Directories/files by name**: `.git`, `.github`, `.gitlab`, `.svn`, `.hg`, `node_modules`, `__pycache__`, `.venv`, `venv`, `env`, `.env`, `.pytest_cache`, `.mypy_cache`, `.ruff_cache`, `.tox`, `.nox`, `build`, `dist`, `.idea`, `.vscode`, `.settings`, `.DS_Store`, `Thumbs.db`, `desktop.ini` (matched case-insensitively), any path component ending in `.egg-info`.
- **Binary extensions**: compiled artifacts, archives, images, audio/video, fonts, databases, office documents, ML artifacts (`.pkl`, `.npy`, `.onnx`, …) and executables (`.exe`, `.msi`, `.bin`, `.dll`, `.so`, …). Files of any other type that contain NUL bytes are treated as binary too and skipped with a warning.
- **Hidden files** (starting with `.`) are ignored, **except**: `.gitignore`, `.gitattributes`, `.gitmodules`, `.env.example`, `.env.template`, `.pylintrc`, `.flake8`, `.coveragerc`, `.dockerignore`, `.editorconfig`, `.pre-commit-config.yaml`, `.python-version`, `.readthedocs.yaml`, `.readthedocs.yml`, `.codecov.yml`.
- **`.gitignore` / `.pyaiignore` rules** (when `py-for-ai[gitignore]` is installed; disable with `--no-gitignore`). Symlinked ignore files are not followed.
- **User patterns** from `--exclude` (matched against the relative POSIX path and the file name). Git-style directory patterns work too: `--exclude 'build/'` excludes the `build/` directory and everything under it. Note: patterns use Python's `fnmatch`, where `*` also matches across `/` (so `docs/*` excludes `docs/deep/file.py` as well).
- **Oversized files** when `--max-file-size` is given.
- **Symlinks resolving outside the project root** and **directory symlinks** are never followed or packed (shown in the tree with a note).
- The **output file itself** is never included in its own pack.

---

## 🪙 Token Estimation

- With `pip install py-for-ai[tokens]`, tokens are counted precisely with `tiktoken` using the `cl100k_base` encoding (used by GPT-3.5/4 families and a reasonable approximation for other models).
- Without it, a widely used heuristic of ~4 characters per token is applied, and the CLI tells you which method was used.

---

## 🛠️ Requirements & Dependencies

- Python `3.8` or higher (verified in CI on 3.8–3.14, Linux/Windows/macOS).
- `pyperclip` (for clipboard operations; requires `xclip`/`xsel` on X11 or `wl-clipboard` on Wayland for Linux desktops — otherwise a warning is shown and the output file is still produced).

---

## 🤝 Contributing

```bash
git clone https://github.com/Maksum867/py-for-ai.git
cd py-for-ai
pip install -e ".[dev]"
pytest
```

Please make sure the whole `pytest` suite passes and add tests for new features.


## Development checks

Install the development tools with `python -m pip install -e ".[dev]"`, then run:

```bash
python -m pytest                 # run behavioural tests
ruff check src tests             # detect lint issues
mypy src/py_ai                   # check annotated types
```

These tools complement one another: tests exercise behaviour, Ruff detects
common code issues, and mypy checks types without executing the program.
Passing all checks does not guarantee correctness.

## PyPI publishing (maintainers)

`.github/workflows/publish.yml` publishes **py-for-ai** when a GitHub release
is published, including a prerelease. A tag push alone does not trigger it.
The release must have a non-empty description and a tag matching the package
version (for example, `v0.5.0`). No long-lived PyPI API token is needed: PyPI Trusted Publishing uses
GitHub's short-lived OIDC identity. Publication is a real external release.

1. For an existing PyPI project, open **Your projects → py-for-ai → Manage →
   Publishing** and add a GitHub trusted publisher. For a new project, use
   **Account settings → Publishing → Add a pending publisher** and enter the
   exact project name `py-for-ai`. You must own the existing project or have
   access to publish it; an already-claimed name cannot be reserved by others.
2. Set **Owner** to `Maksum867`, **Repository name** to `py-for-ai`, **Workflow
   name** to `publish.yml` (filename only), and **Environment name** to `pypi`.
3. In GitHub repository **Settings → Environments**, configure `pypi`. Where
   supported by the repository plan, add required reviewers and restrict the
   allowed deployment tags; protect release tags against unauthorized pushes.
   GitHub environment protection availability depends on plan/visibility.
4. Prepare a new version in `pyproject.toml` and the fallback in
   `src/py_ai/__init__.py`, update the changelog, merge reviewed changes, and
   wait for the regular CI checks to succeed. PyPI does not allow overwriting
   an existing distribution/version.
5. Only when ready to release, publish a GitHub release with a non-empty
   description and a matching tag (for example, `v0.5.0` for version `0.5.0`).
   A release for an existing tag also triggers the workflow; a draft does not
   until it is published. The workflow validates the description and tag, tests
   on Linux with Python 3.8 and 3.13, builds wheel and sdist, runs `twine check`,
   and publishes the built artifacts after any environment approval. The release
   workflow does not wait for the separate full OS/Python CI matrix; review
   that matrix before publishing the release. Check Actions and the PyPI page.

References: https://docs.pypi.org/trusted-publishers/ and
https://github.com/pypa/gh-action-pypi-publish. No workflow can be fully
verified until the GitHub environment and PyPI publisher are configured.

**Security:** ignore files are only honored when `pathspec` is installed.
Exclusions are not a substitute for reviewing output for secrets. Writing an
output path overwrites an existing file; choose it carefully. Output is written
to a temporary file in the same directory and atomically replaced on success,
so interrupted writes do not normally truncate an existing output. Existing
file permissions are retained; a new output is created with restrictive
permissions where supported. Atomic replacement breaks existing hardlinks to
the old output file.
