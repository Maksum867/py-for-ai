"""
stripping.py module
===================
Optional source slimming for the Py-For-AI utility (--strip-docs).

Removes comments and docstrings from Python source so the packed context
becomes smaller. The transformation is AST-based and fails safe: any file
that cannot be parsed (syntax error, exotic syntax) is returned unchanged —
packing must never break because of stripping.
"""

from __future__ import annotations

import ast
import io
import sys
import tokenize

_DOCSTRING_NODES = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)


def _collect_and_remove_docstrings(tree) -> list:
    """
    Removes module/class/function docstrings from the AST in place.

    :param tree: Parsed AST.
    :return: List of (lineno, end_lineno) spans of the removed docstrings
             (used by the Python 3.8 fallback, which cannot ast.unparse).
    """
    spans = []
    for node in ast.walk(tree):
        if isinstance(node, _DOCSTRING_NODES):
            body = node.body
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                end = getattr(body[0], "end_lineno", None) or body[0].lineno
                spans.append((body[0].lineno, end))
                # An empty def/class/module still needs a statement.
                node.body = body[1:] if len(body) > 1 else [ast.Pass()]
    return spans


def _fallback_strip(source: str, docstring_spans) -> str:
    """Remove docstrings and comments by position without changing literals.

    If a transformation cannot be proved safe, return the original source.
    The unused spans argument is retained for compatibility with callers.
    """
    del docstring_spans
    try:
        tree = ast.parse(source)
        lines = source.splitlines(keepends=True)
        offsets = [0]
        for line in lines:
            offsets.append(offsets[-1] + len(line))

        def position(line, column, byte_column=False):
            text = lines[line - 1]
            if byte_column:
                column = len(text.encode("utf-8")[:column].decode("utf-8"))
            return offsets[line - 1] + column

        edits = []
        for node in ast.walk(tree):
            if not isinstance(node, _DOCSTRING_NODES) or not node.body:
                continue
            expr = node.body[0]
            if not (isinstance(expr, ast.Expr) and isinstance(expr.value, ast.Constant)
                    and isinstance(expr.value.value, str)):
                continue
            if getattr(expr, "end_lineno", None) is None:
                return source
            start = position(expr.lineno, expr.col_offset, True)
            end = position(expr.end_lineno, expr.end_col_offset, True)
            if isinstance(node, ast.Module):
                # Do not insert a statement before a future import. If a
                # semicolon shares the line, retain the source rather than
                # manufacture invalid syntax or delete unrelated statements.
                if source[end:].lstrip(" \t").startswith(";"):
                    return source
                replacement = ""
            else:
                replacement = "pass"
            edits.append((start, end, replacement))
        for token in tokenize.generate_tokens(io.StringIO(source).readline):
            if token.type == tokenize.COMMENT:
                edits.append((position(*token.start), position(*token.end), ""))
        result = source
        for start, end, replacement in sorted(edits, reverse=True):
            result = result[:start] + replacement + result[end:]
        ast.parse(result)
        # Preserve future-import placement and other compile-time constraints.
        compile(source, "<source>", "exec")
        compile(result, "<stripped>", "exec")
        return result
    except (SyntaxError, ValueError, UnicodeError, IndexError, tokenize.TokenError,
            IndentationError, RecursionError):
        return source


def strip_python_docs(source: str) -> str:
    """
    Returns Python source without comments and docstrings.

    - Comments are removed (AST round-trip on Python >= 3.9, tokenize-based
      blanking on 3.8).
    - Module/class/function docstrings are removed; a def/class left empty
      gets a ``pass`` so the code stays syntactically valid.
    - Regular string literals (including ones containing '#') are preserved.
    - Files that fail to parse are returned unchanged.
    """
    if not source.strip():
        return source
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError, RecursionError):
        return source

    docstring_spans = _collect_and_remove_docstrings(tree)

    if sys.version_info >= (3, 9):
        try:
            return ast.unparse(tree) + "\n"
        except Exception:
            pass

    return _fallback_strip(source, docstring_spans)
