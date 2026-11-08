"""
filters.py module
=================
Filtering logic for the Py-For-AI utility:

- Built-in ignore rules (should_ignore): VCS folders, caches, build
  artifacts, binary extensions, hidden files (with an allowlist).
- Security rule: anything resolving outside of the project root (symlinks)
  is always ignored.
- User-provided glob exclude patterns (--exclude).
- Optional support for .gitignore / .pyaiignore files when the 'pathspec'
  package is installed (extra: pip install py-for-ai[gitignore]).
"""

from __future__ import annotations

import fnmatch
import os
import sys
from pathlib import Path
from typing import Any

# Standard lists of exclusions for ignore checks
IGNORED_NAMES = {
    # Version control and system folders
    '.git', '.github', '.gitlab', '.svn', '.hg', 'node_modules',
    # Python runtime and caches
    '__pycache__', '.venv', 'venv', 'env', '.env', '.pytest_cache',
    '.mypy_cache', '.ruff_cache', '.tox', '.nox', '.egg-info', 'build', 'dist',
    # IDE and editor settings
    '.idea', '.vscode', '.settings',
    # OS system files
    '.DS_Store', 'Thumbs.db', 'desktop.ini'
}

IGNORED_EXTENSIONS = {
    # Compiled Python and binary artifacts
    '.pyc', '.pyo', '.pyd', '.class', '.o', '.obj', '.dll', '.so', '.dylib',
    '.a', '.lib', '.exe', '.msi', '.apk', '.dmg', '.iso', '.img', '.bin',
    '.dat', '.wasm',
    # Archives/Compressed files
    '.zip', '.tar', '.gz', '.bz2', '.xz', '.rar', '.7z', '.tgz', '.jar',
    '.war', '.ear',
    # Images and multimedia
    # NB: '.svg' is deliberately NOT here — SVG is a text/XML format that is
    # often useful as LLM context; true binary content is still caught by the
    # NUL-byte heuristic in readers.py.
    '.png', '.jpg', '.jpeg', '.gif', '.bmp', '.ico', '.webp', '.tiff',
    '.mp3', '.wav', '.ogg', '.flac', '.mp4', '.mkv', '.avi', '.mov', '.webm',
    # Fonts
    '.woff', '.woff2', '.ttf', '.eot', '.otf',
    # Databases, binary documents and ML artifacts
    '.db', '.sqlite', '.sqlite3', '.pdf', '.epub', '.pkl', '.pickle',
    '.joblib', '.npy', '.npz', '.parquet', '.onnx', '.pt', '.h5', '.hdf5',
    # Office documents
    '.docx', '.xlsx', '.pptx', '.doc', '.xls', '.ppt'
}

# File names that are ignored even when they are plain FILES (not
# directories). The generic IGNORED_NAMES set contains *directory* names
# (build, dist, env, venv, node_modules, ...) and must NOT be applied to
# regular files: a legitimate script named 'dist' or 'env' would otherwise
# be silently dropped from the pack.
IGNORED_FILE_NAMES = {
    # OS junk files
    '.DS_Store', 'Thumbs.db', 'desktop.ini',
    # Secrets files (also covered by the hidden-file rule; kept for clarity)
    '.env',
}

# Hidden files (starting with a dot) are ignored by default, EXCEPT these
# explicitly allowed, commonly useful configuration files.
# Stored lowercase; the comparison in should_ignore() is case-insensitive.
ALLOWED_HIDDEN_FILES = {
    '.gitignore', '.gitattributes', '.gitmodules',
    '.env.example', '.env.template',
    '.pylintrc', '.flake8', '.coveragerc',
    '.dockerignore', '.editorconfig',
    '.pre-commit-config.yaml', '.python-version',
    '.readthedocs.yaml', '.readthedocs.yml', '.codecov.yml',
}

# Names of ignore files honored when the optional 'pathspec' dependency
# is installed, in priority order.
IGNORE_FILE_NAMES = ('.pyaiignore', '.gitignore')


def _is_outside_root(path: Path, root_dir: Path) -> bool:
    """
    Checks whether the fully-resolved path escapes the project root.

    :param path: Path to check.
    :param root_dir: Root directory of the project.
    :return: True if the resolved path points outside the root directory
             (only possible through symlinks) or cannot be resolved at all.
    """
    try:
        path.resolve().relative_to(root_dir.resolve())
        return False
    except (ValueError, OSError):
        # ValueError: not a subpath of the root.
        # OSError: unresolvable (e.g. too many symlink levels, ELOOP).
        return True


def should_ignore(path: Path, root_dir: Path, *, _trusted_parent: bool = False) -> bool:
    """
    Checks if a file or directory should be ignored based on the built-in
    exclusion rules.

    Anything resolving outside of the project root (e.g. a symlink pointing to
    /etc or $HOME) is always ignored, so files outside the packed project can
    never leak into the generated context.

    :param path: Path to the file or folder to verify.
    :param root_dir: Root directory of the project.
    :return: True if the path should be ignored, False otherwise.
    """
    # Fast path for the common case: an absolute, non-symlink entry of a real
    # in-root directory cannot resolve outside the root, so the relative parts
    # can be computed directly — this skips two expensive Path.resolve() calls
    # per item (resolve() costs O(depth), which is quadratic on deeply nested
    # projects). Anything unusual falls back to the full resolve-based logic.
    if _trusted_parent and path.is_absolute() and not path.is_symlink():
        try:
            rel_parts_path = path.relative_to(root_dir.resolve())
        except (ValueError, OSError):
            rel_parts_path = None  # fall through to the slow path below
        if rel_parts_path is not None:
            return _should_ignore_rel(path, rel_parts_path)

    # Apply built-in rules to the visible path as well as its target: resolving
    # first would allow a hidden or ignored symlink name to bypass the rules.
    try:
        lexical = path.absolute().relative_to(root_dir.absolute())
    except ValueError:
        lexical = None
    if lexical is not None and _should_ignore_rel(path, lexical):
        return True

    try:
        # Resolve to absolute paths to avoid issues with relative path operations
        abs_root = root_dir.resolve()
        abs_path = path.resolve()
    except OSError:
        # Unresolvable path (dangling symlink chain, ELOOP, etc.): play safe.
        return True

    try:
        # Calculate path relative to the root directory
        rel_path = abs_path.relative_to(abs_root)
    except ValueError:
        # The path (typically via a symlink) points outside of the project
        # root. Ignore it so external files are never packed.
        return True

    return _should_ignore_rel(path, rel_path)


def _should_ignore_rel(path: Path, rel_path) -> bool:
    """Applies the built-in name/extension rules to an already-computed
    relative path (see should_ignore)."""
    is_file = path.is_file()

    # Check each part of the relative path.
    # If any parent folder is in the ignored list, ignore the entire subtree.
    # The LAST component is the item's own name: for regular files it must
    # NOT be matched against the directory-oriented IGNORED_NAMES set (a
    # legitimate script named 'dist' or 'env' would be silently dropped);
    # file-specific junk names are handled below.
    parts = rel_path.parts
    for index, part in enumerate(parts):
        is_last = index == len(parts) - 1
        lowered = part.lower()
        if lowered in IGNORED_NAMES and not (is_file and is_last):
            return True

        # Python packaging artifacts are named "<pkg>.egg-info", not ".egg-info"
        if lowered.endswith('.egg-info'):
            return True

        # Ignore any hidden folders/files (starting with a dot),
        # except explicitly allowed configuration files such as
        # .gitignore, .env.example or .editorconfig
        if part.startswith('.') and lowered not in ALLOWED_HIDDEN_FILES:
            return True

    # Additional file-level checks for extensions and file names.
    # Only FILE-specific names are checked here (IGNORED_FILE_NAMES);
    # directory names from IGNORED_NAMES must never filter regular files.
    if is_file:
        if path.suffix.lower() in IGNORED_EXTENSIONS:
            return True
        if path.name.lower() in IGNORED_FILE_NAMES:
            return True

    return False


def _find_ignore_files(root_dir: Path) -> list[tuple[Path, str]]:
    """
    Collects every '.pyaiignore' / '.gitignore' file inside the project.

    The traversal is iterative (explicit stack), so arbitrarily deep
    projects do not raise RecursionError on Python <= 3.11.

    :param root_dir: Root directory of the project.
    :return: List of ``(base_dir, file_name)`` pairs, ordered so that rules
             applied later win (git semantics): root files first, then deeper
             files; at the same level '.pyaiignore' (tool-specific) comes
             after '.gitignore' so it can override it.
    """
    found: list[tuple[Path, str]] = []
    # ITERATIVE traversal (explicit stack): os.walk recurses per directory
    # level, which hits RecursionError on very deep projects on Python
    # <= 3.11 (caught by the CI matrix). Symlinked directories are not
    # descended into (os.walk default), ignored subtrees are pruned.
    stack: list[Path] = [root_dir]
    while stack:
        base = stack.pop()
        try:
            entries = list(os.scandir(base))
        except OSError:
            continue  # unreadable directory: no ignore files inside
        dirnames: list[str] = []
        filenames: list[str] = []
        for entry in entries:
            try:
                if entry.is_dir(follow_symlinks=False):
                    dirnames.append(entry.name)
                else:
                    filenames.append(entry.name)
            except OSError:
                continue  # vanished or unreadable entry: skip it
        dirnames = [
            d for d in dirnames
            if d.lower() not in IGNORED_NAMES and not d.lower().endswith(".egg-info")
        ]
        for name in IGNORE_FILE_NAMES:
            if name in filenames:
                found.append((base, name))
        for d in dirnames:
            stack.append(base / d)

    def _key(item: tuple[Path, str]) -> tuple:
        depth = len(item[0].relative_to(root_dir).parts)
        # .gitignore (0) before .pyaiignore (1) at the same depth.
        is_pyaiignore = item[1] == ".pyaiignore"
        return (depth, is_pyaiignore, item[0].as_posix())

    found.sort(key=_key)
    return found


def _rebase_ignore_line(line: str, rel_dir: str) -> str:
    """
    Rebases one ignore pattern so it is relative to the project root.

    Git semantics:
    - patterns inside 'sub/.gitignore' apply only inside 'sub/';
    - an UNANCHORED pattern (no '/' except an optional trailing one, e.g.
      '*.tmp' or 'build/') matches at ANY depth below 'sub/', so it must be
      rebased to 'sub/**/<pattern>' — a plain 'sub/<pattern>' would anchor
      it to the direct children of 'sub/' only (deviation from git);
    - an ANCHORED pattern (contains an inner '/', e.g. '/foo' or 'a/b')
      is relative to the ignore file's own dir and is rebased to
      'sub/<pattern>'.
    The '!' negation prefix is preserved. Comments/blank lines pass through.
    """
    # Git preserves leading spaces and escaped trailing spaces. Do not strip
    # the pattern: pathspec handles unescaped trailing whitespace itself.
    if not line.strip() or line.startswith("#"):
        return line
    if not rel_dir:
        return line

    negate = ""
    pattern = line
    if pattern.startswith("!"):
        negate = "!"
        pattern = pattern[1:]
    # An inner slash (ignoring an optional trailing one) anchors the pattern
    # to the ignore file's own directory; without it git matches any depth.
    anchored = "/" in pattern[:-1]
    pattern = pattern.lstrip("/")
    if not pattern:
        return line
    # Directory names are literal paths, not glob patterns.
    literal_dir = "".join("\\" + c if c in "\\*?[]" else c for c in rel_dir)
    if anchored:
        return f"{negate}{literal_dir}/{pattern}"
    return f"{negate}{literal_dir}/**/{pattern}"


def load_ignore_matcher(root_dir: Path):
    """
    Loads all '.pyaiignore' / '.gitignore' files inside the project into a
    SINGLE matcher, when the optional 'pathspec' package is available.

    Merging everything into one PathSpec is important: negation rules ('!')
    only work within one spec, so separate specs would make it impossible for
    '.pyaiignore' to re-include / override rules from '.gitignore' (and vice
    versa). Nested ignore files are discovered and their patterns are rebased
    relative to the project root.

    :param root_dir: Root directory of the project.
    :return: A 'pathspec.PathSpec' matcher, or None when the dependency is
             missing or no ignore files exist.
    """
    root_dir = Path(root_dir)
    found_files = _find_ignore_files(root_dir)
    if not found_files:
        return None

    try:
        import pathspec
    except ImportError:
        names = ", ".join(f"{base.name}/{name}" if base != root_dir else name
                          for base, name in found_files[:5])
        if len(found_files) > 5:
            names += f", ... ({len(found_files)} files total)"
        print(
            f"Note: ignore file(s) ({names}) found, but the optional 'pathspec' package is not "
            f"installed. Install it with 'pip install py-for-ai[gitignore]' to respect them.",
            file=sys.stderr,
        )
        return None

    all_lines: list[str] = []
    for base_dir, name in found_files:
        ignore_file = base_dir / name
        if ignore_file.is_symlink():
            print(f"Warning: ignoring symlinked ignore file '{ignore_file}'.", file=sys.stderr)
            continue
        try:
            lines = ignore_file.read_text(encoding="utf-8-sig", errors="replace").splitlines()
        except OSError as e:
            print(f"Warning: could not read '{ignore_file}': {e}", file=sys.stderr)
            continue
        if base_dir == root_dir:
            rel_dir = ""
        else:
            rel_dir = base_dir.relative_to(root_dir).as_posix()
        all_lines.extend(_rebase_ignore_line(line, rel_dir) for line in lines)
        all_lines.append("")  # blank separator between ignore files

    # pathspec >= 0.12 renamed the 'gitwildmatch' factory to 'gitignore';
    # fall back to the legacy name for older versions.
    spec: Any
    try:
        spec = pathspec.PathSpec.from_lines("gitignore", all_lines)
    except Exception:
        spec = pathspec.PathSpec.from_lines("gitwildmatch", all_lines)
    return spec


def matches_user_patterns(path: Path, root_dir: Path, patterns) -> bool:
    """
    Checks a path against user-provided glob patterns (--exclude).
    Patterns are matched (fnmatch) against the POSIX relative path and
    against the bare file name, so both 'docs/*' and '*.log' work.

    Git-style directory patterns are supported: a trailing slash ('build/')
    matches the directory itself and everything under it.

    NOTE: unlike gitignore, Python's fnmatch '*' matches across '/', so
    'docs/*' also excludes 'docs/deep/file.py'.

    :param path: Path to check.
    :param root_dir: Root directory of the project.
    :param patterns: Iterable of glob patterns.
    :return: True if any pattern matches.
    """
    if not patterns:
        return False
    try:
        rel_posix = path.relative_to(root_dir).as_posix()
    except ValueError:
        rel_posix = path.as_posix()
    name = path.name
    for pattern in patterns:
        if pattern.endswith("/"):
            # Directory pattern ('build/'): match the directory itself (only
            # if `path` IS a directory — a same-named file must not match) or
            # anything under it. This keeps git-style semantics.
            pat = pattern[:-1]
            if not pat:
                continue
            if rel_posix.startswith(pat + "/"):
                return True
            if path.is_dir() and rel_posix == pat:
                return True
        elif fnmatch.fnmatch(rel_posix, pattern) or fnmatch.fnmatch(name, pattern):
            return True
    return False


def matches_ignore_files(path: Path, root_dir: Path, matcher) -> bool:
    """
    Checks a path against the .gitignore/.pyaiignore matcher (if loaded).

    :param path: Path to check.
    :param root_dir: Root directory of the project.
    :param matcher: A pathspec.PathSpec instance or None.
    :return: True if the matcher ignores the path.
    """
    if matcher is None:
        return False
    try:
        rel_posix = path.relative_to(root_dir).as_posix()
    except ValueError:
        return False
    # A plain pattern or a directory pattern ('build/' matches only with a
    # trailing slash) can ignore the path.
    return (
        matcher.match_file(rel_posix)
        or (path.is_dir() and matcher.match_file(rel_posix + "/"))
    )
def _include_pattern_parts(pattern: str):
    """
    Normalizes one --include pattern into a (base_dir, name_glob) pair:

      'src/', 'src/**', 'src'   ->  ('src', None)     everything under src/
      '*.py', 'main.py'         ->  ('**', '*.py')    name glob at any depth
      'src/**/*.py'             ->  ('src', '*.py')   glob under src/, any depth
      'a/b*.txt'                ->  ('a', 'b*.txt')   glob under a/ (fnmatch, so
                                                      '*' crosses '/' — see the
                                                      --exclude note above)
      '', '**'                  ->  ('**', None)      everything
    """
    p = pattern.strip().replace("\\", "/")
    while p.startswith("./"):
        p = p[2:]
    if not p or p == "**":
        return ("**", None)
    trailing = p.endswith(("/", "/**"))
    core = p[:-3] if p.endswith("/**") else p.rstrip("/")
    if not core or core == "**":
        return ("**", None)
    if trailing:
        return (core, None)
    if "/" not in core:
        return ("**", core)
    deep = core.split("/**/", 1)
    if len(deep) == 2:
        return (deep[0], deep[1])
    base, name = core.rsplit("/", 1)
    return (base or "**", name)


def matches_include_patterns(path: Path, root_dir: Path, patterns) -> bool:
    """
    FILES ONLY: True when the file matches at least one --include pattern.
    With no patterns every file is allowed (include filtering is opt-in).

    :param path: Path to check.
    :param root_dir: Root directory of the project.
    :param patterns: Iterable of include glob patterns.
    :return: True if the file should be PACKED.
    """
    if not patterns:
        return True
    try:
        rel = path.relative_to(root_dir).as_posix()
    except ValueError:
        rel = path.as_posix()
    name = path.name
    for pattern in patterns:
        base, glob_ = _include_pattern_parts(pattern)
        if base == "**":
            # A bare literal can name a root directory as well as a file.
            if (glob_ and not any(c in glob_ for c in "*?[")
                    and rel.startswith(glob_ + "/")):
                return True
            if glob_ is None or fnmatch.fnmatch(name, glob_) or fnmatch.fnmatch(rel, glob_):
                return True
        elif glob_ is None:
            # The pattern names a directory (possibly with glob characters);
            # compare each ancestor rather than the full file path.
            parents = rel.split("/")[:-1]
            if any(fnmatch.fnmatch("/".join(parents[:i]), base)
                   for i in range(1, len(parents) + 1)):
                return True
        else:
            if fnmatch.fnmatch(rel, base + "/" + glob_):
                return True
            if rel.startswith(base + "/") and fnmatch.fnmatch(name, glob_):
                return True
    return False


def dir_kept_for_include(path: Path, root_dir: Path, patterns) -> bool:
    """
    DIRECTORIES: True when the directory may still contain included files
    (it equals/contains an include base or is an ancestor of it, or the
    pattern is a bare name glob that can match at any depth). Keeps traversal
    and the directory tree consistent with --include.
    """
    if not patterns:
        return True
    try:
        rel = path.relative_to(root_dir).as_posix()
    except ValueError:
        return True
    for pattern in patterns:
        base, _ = _include_pattern_parts(pattern)
        if base == "**":
            return True
        if any(c in base for c in "*?["):
            return True  # wildcard directory components: do not prune potentially matching paths
        if rel == base or rel.startswith(base + "/") or base.startswith(rel + "/"):
            return True
    return False


def make_filter(root_dir: Path, patterns=(), matcher=None, include_patterns=()):
    """
    Builds a single ignore predicate combining all filtering layers:
    built-in rules -> user glob patterns (--exclude) -> .gitignore/.pyaiignore
    matcher -> --include gate (when include patterns are given, only matching
    files survive; directories that cannot contain them are treated as
    ignored, so they are pruned from traversal and from the tree).

    :param root_dir: Root directory of the project.
    :param patterns: User-provided glob patterns (--exclude).
    :param matcher: Optional pathspec matcher (load_ignore_matcher).
    :param include_patterns: Optional include glob patterns (--include).
    :return: Callable[[Path], bool]; True means 'ignore'.
    """
    root_dir = Path(root_dir)
    inc = tuple(include_patterns or ())
    patterns = tuple(patterns or ())  # generators must work for every entry

    def _ignore(path: Path) -> bool:
        if (
            should_ignore(path, root_dir, _trusted_parent=True)
            or matches_user_patterns(path, root_dir, patterns)
            or matches_ignore_files(path, root_dir, matcher)
        ):
            return True
        if path.is_symlink() and path.is_file():
            # A link must not bypass exclusions applied to its target or any
            # of the target's parent directories. Includes still match the alias.
            try:
                target = path.resolve()
                target.relative_to(root_dir.resolve())
            except (OSError, ValueError, RuntimeError):
                return True
            for candidate in (target, *target.parents):
                if candidate == root_dir:
                    break
                if (should_ignore(candidate, root_dir, _trusted_parent=True)
                        or matches_user_patterns(candidate, root_dir, patterns)
                        or matches_ignore_files(candidate, root_dir, matcher)):
                    return True
        if inc:
            if path.is_dir():
                return not dir_kept_for_include(path, root_dir, inc)
            return not matches_include_patterns(path, root_dir, inc)
        return False

    return _ignore
