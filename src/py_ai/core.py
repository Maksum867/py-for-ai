"""
core.py module
==============
Core orchestration logic for the Py-For-AI utility:
- Project directory tree generation in ASCII format (build_tree_lines).
- Gathering allowed file contents and writing them to the final context file.
- Copying the generated output to the system clipboard.

Design notes
------------
- Traversal (walker and tree builder) is ITERATIVE (explicit stack), so
  extremely deep projects do not hit Python's recursion limit. Files are
  collected in the same deterministic order as the classic recursive DFS:
  for every directory - subdirectories (with their subtrees) first, then
  files, all sorted case-insensitively.
- Symlinks resolving outside of the project root are never followed or
  packed (see filters.py); cycles/duplicates are traversed once.
- Backwards compatibility: the public names 'should_ignore',
  'IGNORED_NAMES', 'IGNORED_EXTENSIONS', 'ALLOWED_HIDDEN_FILES',
  'read_text_content', 'build_tree_lines' and 'pack_project' remain
  available from 'py_ai.core'.
"""

# PEP 563: postpone evaluation of annotations. Keeps `str | Path` (PEP 604)
# and builtin-generic annotations importable on Python 3.8 and 3.9.
from __future__ import annotations

import os
import stat
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

import pyperclip

from py_ai.filters import (
    ALLOWED_HIDDEN_FILES,
    IGNORED_EXTENSIONS,
    IGNORED_NAMES,
    _is_outside_root,
    load_ignore_matcher,
    make_filter,
    should_ignore,
)
from py_ai.formatting import (
    assemble_json_output,
    assemble_output,
    available_formats,
    format_file_block,
)
from py_ai.readers import read_text_content
from py_ai.stripping import strip_python_docs
from py_ai.tokens import count_lines, count_tokens

# Internal sentinel used in the tree stack for directory symlinks; the final
# human-readable note is chosen when the entry is popped and rendered.
_DIR_SYMLINK_MARKER = "__pyai_dir_symlink__"

# Default output file names that must never be packed, even when they exist
# in the project from a previous run with a different output path.
_DEFAULT_OUTPUT_NAMES = {"ai_context.txt"}

__all__ = [
    "ALLOWED_HIDDEN_FILES",
    "IGNORED_EXTENSIONS",
    "IGNORED_NAMES",
    "build_tree_lines",
    "pack_project",
    "read_text_content",
    "should_ignore",
]


def _is_same_path(first: Path, second: Path) -> bool:
    """
    Safely compares two paths by their resolved location. Never raises,
    even for broken symlinks or non-existent paths.

    For entries whose own name is not a symlink the comparison uses
    ``abspath`` (pure string normalization): ``Path.resolve()`` is recursive
    on Python <= 3.11 and raises RecursionError on very deep projects
    (caught by the CI matrix on Python 3.8-3.11). Symlink-bearing paths
    still go through ``resolve()`` so aliases compare by their real target.
    """
    try:
        if not first.is_symlink() and not second.is_symlink():
            return (os.path.normcase(os.path.abspath(first))
                    == os.path.normcase(os.path.abspath(second)))
        return first.resolve() == second.resolve()
    except OSError:
        return os.path.abspath(first) == os.path.abspath(second)


def _link_suffix(path: Path) -> str:
    """Returns a ' -> target' suffix for symlinks, or an empty string."""
    if path.is_symlink():
        try:
            return f" -> {os.readlink(path)}"
        except OSError:
            return " -> <unreadable link>"
    return ""


def _dir_identity(path: Path):
    """
    Returns a stable identity key for a directory (used to detect duplicate
    traversal / cycles), or None when it cannot be determined.

    Prefers the ``(st_dev, st_ino)`` pair, but falls back to the resolved
    absolute path on filesystems where inodes are unreliable (e.g. ``st_ino``
    is 0 or repeated on some network/FUSE mounts). Relying on inodes alone
    caused *silent* loss of whole subtrees there: every directory after the
    first was treated as a duplicate and skipped.
    """
    try:
        st = path.stat()
    except OSError:
        return None
    if st.st_dev and st.st_ino:
        return ("inode", st.st_dev, st.st_ino)
    try:
        return ("path", os.path.normcase(os.path.realpath(os.fspath(path))))
    except OSError:
        return None


def _file_identity(path: Path):
    """
    Returns a stable identity key for a file (or a file symlink), or None when
    it cannot be determined. Used to skip symlink aliases so the same content
    is never packed twice (mirrors the directory-symlink policy). Prefers the
    ``(st_dev, st_ino)`` pair with a resolved-path fallback for filesystems
    with unreliable inodes.
    """
    try:
        st = path.stat()  # follows symlinks: alias == target
    except OSError:
        try:
            return ("path", os.path.normcase(os.path.realpath(os.fspath(path))))
        except OSError:
            return None
    if st.st_dev and st.st_ino:
        return ("inode", st.st_dev, st.st_ino)
    try:
        return ("path", os.path.normcase(os.path.realpath(os.fspath(path))))
    except OSError:
        return None


def _dir_has_visible_content(directory: Path, ignore_predicate, root_dir: Path,
                            exclude_resolved: Path | None, memo: dict) -> bool:
    """
    True when the directory still contains at least one entry the tree would
    show: a non-ignored file/dir, or an entry displayed with an explanatory
    note (outside-root symlink, dangling symlink, directory symlink).

    Used to prune directories whose entire content was filtered out (e.g. by
    ``--exclude 'docs/*'``) so the tree stays consistent with the packed
    content instead of showing empty folders.

    ``memo`` caches per-directory results and MUST be shared across all calls
    of one ``build_tree_lines`` run (filters are constant within it). Results
    are computed bottom-up for the whole subtree on a miss, so every
    directory is resolved exactly once per run — total O(n) even for deeply
    nested chains. Unreadable directories are treated as visible (they render
    an error note in the tree).
    """
    key = _dir_identity(directory)
    if key is not None and key in memo:
        return memo[key]

    # Post-order traversal; each frame aggregates its children's results.
    # frame = [dir, key_or_None, children_or_None, index, child_results]
    stack = [[directory, key, None, 0, []]]
    while stack:
        frame = stack[-1]
        frame_dir, frame_key, children, idx, child_results = frame

        if children is None:
            try:
                children = list(frame_dir.iterdir())
            except OSError:
                # Cannot look inside: keep the directory visible (it renders
                # an error note in the tree).
                if frame_key is not None:
                    memo[frame_key] = True
                stack.pop()
                if stack:
                    stack[-1][4].append(True)
                continue
            frame[2] = children

        if idx >= len(children):
            visible = any(child_results)
            if frame_key is not None:
                memo[frame_key] = visible
            stack.pop()
            if stack:
                stack[-1][4].append(visible)
            continue

        frame[3] = idx + 1
        child = children[idx]

        if child.is_symlink() and _is_outside_root(child, root_dir):
            child_results.append(True)  # rendered with an "outside root" note
            continue
        if child.is_symlink() and not child.exists():
            child_results.append(True)  # rendered with a "dangling symlink" note
            continue
        if ignore_predicate(child):
            child_results.append(False)
            continue
        if exclude_resolved is not None and _is_same_path(child, exclude_resolved):
            child_results.append(False)
            continue
        if child.parent == root_dir and child.name in _DEFAULT_OUTPUT_NAMES:
            child_results.append(False)
            continue
        if child.is_symlink():
            # Directory symlink (a file symlink is not a dir): note-worthy.
            child_results.append(True)
            continue
        if child.is_dir():
            child_key = _dir_identity(child)
            if child_key is not None and child_key in memo and memo[child_key]:
                child_results.append(True)
                continue
            stack.append([child, child_key, None, 0, []])
            continue
        child_results.append(True)  # a plain visible file

    return bool(memo.get(key))


def build_tree_lines(path: Path, root_dir: Path, prefix: str = "", is_last: bool = True,
                     is_root: bool = False, exclude_path: Path | None = None,
                     skipped_files: dict[Path, str] | None = None,
                     ignore_predicate=None) -> list[str]:
    """
    Generates the ASCII project tree, skipping ignored files and folders.

    The traversal is ITERATIVE (explicit stack), so arbitrarily deep projects
    do not raise RecursionError. Symlink cycles/duplicates are visited once
    and marked instead of exploding the tree.

    :param path: Path of the root folder to render.
    :param root_dir: Root directory of the project (for filtering checks).
    :param prefix: Prefix for the current line (indentation/connection symbols).
    :param is_last: Whether the current item is the last one in its parent directory.
    :param is_root: Whether the current item is the root folder of the project.
    :param exclude_path: Optional path (e.g. the output file) that must not
                         appear in the tree.
    :param skipped_files: Optional mapping of {file path: reason}. Such files
                          are shown in the tree with a "[skipped: ...]" note so
                          the tree stays consistent with the FILES CONTENT section.
    :param ignore_predicate: Optional callable(Path) -> bool (built-ins only
                             when omitted; use make_filter() to extend).
    :return: List of strings forming the ASCII tree.
    """
    if ignore_predicate is None:
        ignore_predicate = make_filter(root_dir)

    # Shared cache for _dir_has_visible_content(): pruning decisions are
    # constant within one run (same filters), so every directory is resolved
    # exactly once — total O(n) even for deeply nested projects.
    empty_dir_cache: dict = {}

    lines: list[str] = []
    # Keys come from _dir_identity(): either ("inode", st_dev, st_ino) or
    # ("path", resolved) — a plain tuple is the accurate common type.
    visited_dirs: set[tuple] = set()

    exclude_resolved = None
    if exclude_path is not None:
        try:
            exclude_resolved = exclude_path.resolve()
        except OSError:
            exclude_resolved = Path(os.path.abspath(exclude_path))

    # Stack entries: (path, prefix, is_last, is_root, marker)
    # marker, when set, is appended to the item's line and the item's
    # children are never traversed (used for unsafe/outside-root symlinks).
    stack: list[tuple[Path, str, bool, bool, str | None]] = [
        (path, prefix, is_last, is_root, None)
    ]

    while stack:
        current, cur_prefix, cur_is_last, cur_is_root, marker = stack.pop()

        # Resolve the deferred marker for directory symlinks: known-visited
        # targets are cycles/duplicates, the rest are simply not followed.
        if marker == _DIR_SYMLINK_MARKER:
            link_key = _dir_identity(current)
            if link_key is not None and link_key in visited_dirs:
                marker = "cyclic or duplicate link — already traversed, not followed"
            else:
                marker = "directory symlink — not followed"

        if cur_is_root:
            lines.append(f"{current.name}/")
        else:
            connector = "└── " if cur_is_last else "├── "
            is_directory = current.is_dir() if marker is None else False
            display_name = current.name + ("/" if is_directory else "")
            display_name += _link_suffix(current)
            note = ""
            if marker is not None:
                note = f"  [{marker}]"
            elif not is_directory and skipped_files and current in skipped_files:
                note = f"  [skipped: {skipped_files[current]}]"
            lines.append(f"{cur_prefix}{connector}{display_name}{note}")

        if marker is not None:
            # Explicitly blocked item: shown for visibility, never traversed.
            continue

        if current.is_dir():
            # Cycle/duplicate detection: track directories by a reliable
            # identity (inode when available, resolved path otherwise).
            dir_key = _dir_identity(current)

            child_prefix = "" if cur_is_root else cur_prefix + ("    " if cur_is_last else "│   ")

            if dir_key is not None:
                if dir_key in visited_dirs:
                    lines.append(f"{child_prefix}└── [cyclic or duplicate link — already traversed, not followed]")
                    continue
                visited_dirs.add(dir_key)

            try:
                # List directory items, sort them: directories first, then
                # files (case-insensitive). Ignored items are filtered out.
                entries: list[tuple[Path, str | None]] = []
                for item in current.iterdir():
                    # Short-circuit: a non-symlink entry of a real in-root
                    # directory can never resolve outside the root, so skip
                    # the expensive resolve() on the common path.
                    if item.is_symlink() and _is_outside_root(item, root_dir):
                        # Kept visible in the tree, but never followed or packed.
                        entries.append((item, "symlink outside project root — not followed, not packed"))
                        continue
                    if item.is_symlink() and not item.exists():
                        # Dangling link: nothing to pack, but it must stay
                        # visible with an explanatory note (not silently
                        # disappear from the tree).
                        entries.append((item, "dangling symlink — target missing"))
                        continue
                    if ignore_predicate(item):
                        continue
                    if exclude_resolved is not None and _is_same_path(item, exclude_resolved):
                        # The output file itself must never appear in its own pack.
                        continue
                    if item.parent == root_dir and item.name in _DEFAULT_OUTPUT_NAMES:
                        # A leftover pack from a previous run that used the
                        # default output name at the project root. Nested files
                        # with the same name are legitimate and stay packed.
                        continue
                    if item.is_dir() and item.is_symlink():
                        # Directory symlinks are visible in the tree but are
                        # never traversed (see _collect_files). The final note
                        # is decided when the entry is popped, based on whether
                        # its target directory has already been traversed.
                        entries.append((item, _DIR_SYMLINK_MARKER))
                        continue
                    if item.is_dir() and not _dir_has_visible_content(
                            item, ignore_predicate, root_dir, exclude_resolved,
                            empty_dir_cache):
                        # The whole subtree was filtered out (e.g. by --exclude
                        # or .gitignore): prune the empty folder from the tree.
                        continue
                    entries.append((item, None))
                entries.sort(key=lambda entry: (not entry[0].is_dir() if entry[1] is None else True,
                                                entry[0].name.lower(), entry[0].name))
            except OSError as e:
                # Report permission errors or system issues as a child note of
                # the directory instead of duplicating the directory entry.
                reason = e.strerror or str(e)
                lines.append(f"{child_prefix}└── [Cannot access directory: {reason}]")
                continue

            # Push children in reverse order so they are processed (LIFO)
            # in the original sorted order.
            for i in range(len(entries) - 1, -1, -1):
                item, item_marker = entries[i]
                item_is_last = (i == len(entries) - 1)
                stack.append((item, child_prefix, item_is_last, False, item_marker))

    return lines


def _collect_files(root_path: Path, output_path: Path, ignore_predicate) -> tuple[list[Path], dict[Path, str]]:
    """
    Collects all files to pack using an ITERATIVE depth-first traversal.

    The work stack holds both files and directories, which reproduces exactly
    the classic recursive DFS order: for each directory its subdirectories
    (with complete subtrees) come first, then its files, everything sorted
    case-insensitively.

    :param root_path: Root directory (resolved).
    :param output_path: Output file to exclude from packing.
    :param ignore_predicate: Callable(Path) -> bool.
    :return: Tuple (ordered file paths to pack, skipped_links) where
             skipped_links maps dropped symlink aliases to a reason.
    """
    files_to_pack: list[Path] = []
    # Keys come from _dir_identity(): either ("inode", st_dev, st_ino) or
    # ("path", resolved) — a plain tuple is the accurate common type.
    visited_dirs: set[tuple] = set()
    work_stack: list[Path] = [root_path]

    while work_stack:
        node = work_stack.pop()

        if node.is_dir():
            dir_key = _dir_identity(node)
            if dir_key is None:
                continue
            if dir_key in visited_dirs:
                # Symlink cycle or duplicate directory link.
                continue
            visited_dirs.add(dir_key)

            try:
                # Sort items for a deterministic walking order:
                # directories first, then files, both case-insensitive.
                items = sorted(node.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower(), x.name))
            except PermissionError as e:
                print(f"Warning: Permission denied reading directory {node}: {e}", file=sys.stderr)
                continue
            except OSError as e:
                # ELOOP, I/O errors and friends: warn and keep going.
                print(f"Warning: Could not read directory {node}: {e}", file=sys.stderr)
                continue

            # Push in reverse order so the sorted DFS order is preserved
            # when popping (both subdirectories and files go through the
            # same work stack).
            for item in reversed(items):
                if ignore_predicate(item):
                    continue
                if item.is_dir() and item.is_symlink():
                    # Directory symlinks are NEVER followed for packing:
                    # files are only collected through their real paths, so
                    # no aliased paths and no cycles can occur. The symlink
                    # stays visible in the directory tree (see build_tree_lines).
                    continue
                work_stack.append(item)

        elif node.is_file():
            # Avoid packing the output file itself. A leftover pack from a
            # previous run that used the default output name at the project
            # root is excluded too; nested files with that name are legitimate.
            if _is_same_path(node, output_path):
                continue
            if node.parent == root_path and node.name in _DEFAULT_OUTPUT_NAMES:
                continue
            files_to_pack.append(node)

    # File-symlink aliases pointing at an already-collected file are skipped,
    # so identical content is never packed twice (the alias stays visible in
    # the tree with an explanatory note). Real files are always preferred:
    # their identities are collected first and the alias matches one of them.
    real_identities: set = set()
    for f in files_to_pack:
        if not f.is_symlink():
            key = _file_identity(f)
            if key is not None:
                real_identities.add(key)

    unique_files: list[Path] = []
    seen_aliases: set = set()
    skipped_links: dict[Path, str] = {}
    for f in files_to_pack:
        if f.is_symlink():
            key = _file_identity(f)
            if key is not None and (key in real_identities or key in seen_aliases):
                skipped_links[f] = ("symlink alias — content already packed from the real file"
                                    if key in real_identities else
                                    "symlink alias — content already packed from another file")
                continue
            if key is not None:
                seen_aliases.add(key)
        unique_files.append(f)

    return unique_files, skipped_links


def pack_project(root_dir: str | Path, output_file: str | Path, copy_to_clipboard: bool = True,
                 *, max_file_size: int | None = None, output_format: str = "text",
                 exclude_patterns=None, include_patterns=None, respect_gitignore: bool = True,
                 include_tree: bool = True, enable_token_count: bool = True,
                 strip_docs: bool = False, include_type_summary: bool = True,
                 dry_run: bool = False, budget: int | None = None) -> dict:
    """
    Recursively scans the project, builds an ASCII tree, gathers all text file
    contents, saves the aggregated output to a single file and (optionally)
    copies it to the system clipboard.

    :param root_dir: Path to the root folder of the project.
    :param output_file: Path to the target output text file (not written in
                        dry-run mode).
    :param copy_to_clipboard: Whether to copy the generated context to the clipboard.
    :param max_file_size: Optional maximum file size in bytes; larger files
                          are skipped with a warning and marked in the tree.
    :param output_format: 'text', 'markdown' or 'json'.
    :param exclude_patterns: Optional iterable of glob patterns (--exclude),
                             matched against the relative POSIX path and the file name.
    :param include_patterns: Optional iterable of glob patterns (--include);
                             when given, ONLY matching files are packed and
                             directories that cannot contain them are pruned.
    :param respect_gitignore: Honor .pyaiignore/.gitignore files when the
                              optional 'pathspec' dependency is installed.
    :param include_tree: When False, the directory tree is omitted from the output.
    :param enable_token_count: When False, token estimation is skipped (faster
                               on large projects); token_method becomes 'disabled'.
    :param strip_docs: Strip comments and docstrings from Python files
                       (syntax-error files pass through untouched).
    :param include_type_summary: When False, the per-extension summary is
                                 omitted from the header / JSON document.
    :param dry_run: Preview mode — compute everything and report, but write
                    no file and touch no clipboard.
    :param budget: Optional token budget; when the pack exceeds it a warning
                   (with the heaviest files) is printed and 'budget_exceeded'
                   is set in the returned stats. Requires token counting.
    :return: Dictionary with execution statistics (file counts, size, lines,
             estimated tokens, per-file and per-extension stats, clipboard
             status, ...).
    """
    if output_format not in available_formats():
        raise ValueError(
            f"Unsupported output format '{output_format}'. "
            f"Available formats: {', '.join(available_formats())}."
        )
    if max_file_size is not None and max_file_size <= 0:
        raise ValueError(f"max_file_size must be positive, got {max_file_size!r}")
    if budget is not None and budget <= 0:
        raise ValueError(f"budget must be a positive number of tokens, got {budget!r}")
    if budget is not None and not enable_token_count:
        raise ValueError("budget check requires token counting (do not disable it)")

    root_path = Path(root_dir).resolve()
    output_path = Path(output_file).resolve()

    if not root_path.exists() or not root_path.is_dir():
        raise FileNotFoundError(f"The specified directory '{root_path}' does not exist or is not a directory.")

    matcher = load_ignore_matcher(root_path) if respect_gitignore else None
    ignore_predicate = make_filter(root_path, patterns=exclude_patterns or (),
                                   matcher=matcher, include_patterns=include_patterns or ())

    # 1. Collect all files allowed for packing (iterative, cycle-safe,
    #    deterministic DFS order). Symlink aliases of already-collected files
    #    are dropped here and reported through skipped_links.
    files_to_pack, skipped_links = _collect_files(root_path, output_path, ignore_predicate)

    # 2. Read file contents, apply optional stripping, collect statistics.
    content_blocks: list[str] = []
    file_records: list[dict] = []
    skipped_files: dict[Path, str] = dict(skipped_links)
    total_source_lines = 0
    stripped_suffixes = (".py", ".pyi")

    for file_path in files_to_pack:
        try:
            rel_path = file_path.relative_to(root_path).as_posix()
        except ValueError:
            rel_path = file_path.as_posix() if hasattr(file_path, "as_posix") else str(file_path)

        # Optional per-file size limit.
        if max_file_size is not None:
            try:
                file_size = file_path.stat().st_size
            except OSError:
                file_size = 0
            if file_size > max_file_size:
                reason = f"file exceeds size limit ({_format_bytes(file_size)} > {_format_bytes(max_file_size)})"
                print(f"Warning: Skipped '{rel_path}' ({reason}).", file=sys.stderr)
                skipped_files[file_path] = f"exceeds size limit ({_format_bytes(max_file_size)})"
                continue

        content, error_reason, encoding = read_text_content(file_path)

        if content is None:
            # Unreadable/undecodable/binary files are skipped gracefully.
            print(f"Warning: Skipped '{rel_path}' ({error_reason}).", file=sys.stderr)
            skipped_files[file_path] = error_reason or "unknown read error"
            continue

        # Be transparent about transcoding: a non-UTF-8 source file is
        # converted to UTF-8 in the pack, which may surprise the user.
        if encoding is not None and not encoding.startswith("utf-8"):
            print(f"Note: '{rel_path}' was read as {encoding} and transcoded to UTF-8.", file=sys.stderr)

        if strip_docs and file_path.suffix.lower() in stripped_suffixes:
            content = strip_python_docs(content)

        record_lines = count_lines(content)
        record_tokens = count_tokens(content)[0] if enable_token_count else None
        total_source_lines += record_lines
        file_records.append({
            "path": rel_path,
            "lines": record_lines,
            "tokens": record_tokens,
            "content": content,
        })
        if output_format != "json":
            content_blocks.append(format_file_block(rel_path, content, output_format))

    packed_count = len(file_records)
    failed_count = len(skipped_files)

    # Per-extension summary.
    file_types: dict[str, dict] = {}
    for record in file_records:
        ext = Path(record["path"]).suffix.lower() or "(no ext)"
        bucket = file_types.setdefault(ext, {"files": 0, "lines": 0, "tokens": 0})
        bucket["files"] += 1
        bucket["lines"] += record["lines"]
        if record["tokens"] is not None:
            bucket["tokens"] += record["tokens"]

    # 3. Generate the ASCII directory tree (AFTER reading files, so entries
    #    that failed to read can be visibly marked).
    tree_text = ""
    if include_tree:
        tree_lines = build_tree_lines(
            root_path,
            root_path,
            is_root=True,
            exclude_path=output_path,
            skipped_files=skipped_files,
            ignore_predicate=ignore_predicate,
        )
        tree_text = "\n".join(tree_lines)

    # 4. Assemble the complete output document and compute statistics.
    #    Token statistics are computed over the FINAL document, so they
    #    describe exactly what would be pasted into the LLM. Embedding the
    #    statistics changes the header, which changes the statistics, so the
    #    assembly is repeated until a fixed point is reached.
    stats: dict[str, Any] = {
        "project_name": root_path.name,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "packed_count": packed_count,
        "failed_count": failed_count,
        "total_lines": 0,
        "estimated_tokens": 0,
        "token_method": "",
        "file_types": file_types,
        "include_type_summary": include_type_summary,
        "budget": budget,
        "budget_exceeded": False,
        "dry_run": dry_run,
    }

    def _assemble(current_stats: dict) -> str:
        if output_format == "json":
            skipped_rel = sorted(
                (p.relative_to(root_path).as_posix(), reason)
                for p, reason in skipped_files.items()
            )
            files_payload = [
                {
                    "path": record["path"],
                    "lines": record["lines"],
                    "tokens": record["tokens"],
                    "content": record["content"],
                }
                for record in file_records
            ]
            return assemble_json_output(current_stats, tree_text, files_payload,
                                        skipped_rel, include_tree=include_tree)
        return assemble_output(current_stats, tree_text, content_blocks,
                               output_format, include_tree=include_tree)

    full_output = _assemble(stats)
    for _ in range(10):
        total_lines = count_lines(full_output)
        if enable_token_count:
            estimated_tokens, token_method = count_tokens(full_output)
        else:
            estimated_tokens, token_method = 0, "disabled"
        if (total_lines, estimated_tokens, token_method) == (
                stats["total_lines"], stats["estimated_tokens"], stats["token_method"]):
            break  # the embedded statistics match the final document exactly
        stats["total_lines"] = total_lines
        stats["estimated_tokens"] = estimated_tokens
        stats["token_method"] = token_method
        full_output = _assemble(stats)

    # Heaviest files (for --budget hints and dry-run previews).
    heaviest_source = sorted(file_records, key=lambda r: -(r["tokens"] or 0))
    stats["heaviest_files"] = [
        {"path": r["path"], "lines": r["lines"], "tokens": r["tokens"]}
        for r in heaviest_source[:5]
    ]

    # Budget check.
    if budget is not None and estimated_tokens > budget:
        stats["budget_exceeded"] = True
        heavy = ", ".join(
            f"'{r['path']}' (~{r['tokens'] or 0:,})" for r in stats["heaviest_files"][:3]
        )
        print(
            f"Warning: Token budget exceeded: ~{estimated_tokens:,} > {budget:,}. "
            f"Heaviest files: {heavy}. Consider excluding some of them.",
            file=sys.stderr,
        )

    # 5. Write to the output file (unless previewing).
    if dry_run:
        disk_size = len(full_output.encode("utf-8"))
    else:
        try:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            # Write beside the destination, then replace it atomically. A failed
            # write must not truncate an existing file, or mutate its hardlinks.
            temporary_path = None
            try:
                with tempfile.NamedTemporaryFile(
                    mode="w", encoding="utf-8", newline="", dir=output_path.parent,
                    prefix=".pyai-", suffix=".tmp", delete=False,
                ) as f:
                    temporary_path = Path(f.name)
                    f.write(full_output)
                if output_path.is_file():
                    os.chmod(temporary_path, stat.S_IMODE(output_path.stat().st_mode))
                os.replace(temporary_path, output_path)
            finally:
                if temporary_path is not None:
                    temporary_path.unlink(missing_ok=True)
        except Exception as e:
            raise OSError(f"Failed to write results to '{output_path}': {e}")

        # Report the REAL on-disk size in bytes (len() counts characters, which
        # understates the size of non-ASCII content, e.g. Cyrillic in UTF-8).
        try:
            disk_size = output_path.stat().st_size
        except OSError:
            disk_size = len(full_output.encode("utf-8"))

        # 6. Copy to the system clipboard if enabled. Any failure is reported
        #    to the caller through the returned statistics (the CLI renders
        #    the warning exactly once) instead of printing here.
        clipboard_copied = False
        clipboard_error = None
        if copy_to_clipboard:
            try:
                pyperclip.copy(full_output)
                clipboard_copied = True
            except Exception as e:
                clipboard_error = str(e)
    if dry_run:
        clipboard_copied, clipboard_error = False, None

    return {
        "root_dir": str(root_path),
        "output_file": str(output_path),
        "packed_count": packed_count,
        "failed_count": failed_count,
        "clipboard_copied": clipboard_copied,
        "clipboard_error": clipboard_error,
        "file_size": disk_size,
        "total_lines": stats["total_lines"],
        "total_source_lines": total_source_lines,
        "estimated_tokens": estimated_tokens,
        "token_method": token_method,
        "output_format": output_format,
        "include_tree": include_tree,
        "files": [{"path": r["path"], "lines": r["lines"], "tokens": r["tokens"]}
                  for r in file_records],
        "file_types": file_types,
        "heaviest_files": stats["heaviest_files"],
        "budget": budget,
        "budget_exceeded": stats["budget_exceeded"],
        "dry_run": dry_run,
    }


def _format_bytes(num_bytes: int) -> str:
    """Formats a byte count as a short human-readable string."""
    if num_bytes < 1024:
        return f"{num_bytes} B"
    if num_bytes < 1024 ** 2:
        return f"{num_bytes / 1024:.1f} KB"
    return f"{num_bytes / (1024 ** 2):.2f} MB"
