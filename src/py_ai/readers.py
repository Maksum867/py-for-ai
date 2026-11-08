"""
readers.py module
=================
Robust reading of project files for the Py-For-AI utility:

- BOM-aware decoding (UTF-32 / UTF-16 / UTF-8-SIG).
- Binary detection via NUL-byte heuristics.
- Encoding fallback chain: UTF-8 -> cp1251 -> latin-1.
"""

from __future__ import annotations

import codecs
from pathlib import Path

# How many leading bytes are inspected for NUL bytes when deciding whether
# a file is binary (a similar heuristic to the one git uses).
BINARY_CHECK_BYTES = 8192

# Encodings tried, in order, when a file is not UTF-8.
ENCODING_FALLBACK_CHAIN = ("utf-8", "utf-8-sig", "cp1251", "latin-1")

# BOM -> encoding mapping. NB: UTF-32 BOMs share their prefix with UTF-16
# BOMs, so UTF-32 must be checked first.
_BOM_ENCODINGS = (
    (codecs.BOM_UTF32_LE, "utf-32"),
    (codecs.BOM_UTF32_BE, "utf-32"),
    (codecs.BOM_UTF16_LE, "utf-16"),
    (codecs.BOM_UTF16_BE, "utf-16"),
    (codecs.BOM_UTF8, "utf-8-sig"),
)


def looks_binary(raw: bytes, check_bytes: int | None = None) -> bool:
    """
    Binary heuristic: text files never contain NUL bytes.

    By default the WHOLE buffer is inspected: the bytes are already in memory,
    and a full scan also catches binary tails that the old leading-8-KiB
    window missed (a NUL is valid UTF-8, so such files would otherwise be
    packed as text with embedded NULs). Pass ``check_bytes`` to inspect only
    a leading window (git-like behaviour).

    Note: UTF-16/32 files WITHOUT a BOM contain NUL bytes and are therefore
    reported as binary — they cannot be reliably told apart from real
    binary data.

    :param raw: Raw file bytes.
    :param check_bytes: How many leading bytes to inspect (None = all).
    :return: True if the content looks binary.
    """
    window = raw if check_bytes is None else raw[:check_bytes]
    return b"\x00" in window


def _normalize_newlines(text: str) -> str:
    """Converts Windows/mac line endings to plain LF.

    Source files created on Windows carry ``\\r\\n``. Packing them as-is would
    bloat the LLM context with CR noise (each CR is extra token material) and
    make statistics platform-dependent. The pack itself is always written with
    LF, so the content is normalized at read time.
    """
    return text.replace("\r\n", "\n").replace("\r", "\n")


def read_text_content(file_path: Path) -> tuple[str | None, str | None, str | None]:
    """
    Reads a file and returns its text content, handling encodings gracefully.

    Detection strategy:
    1. BOM-aware decoding for UTF-32 / UTF-16 / UTF-8-SIG files.
    2. Files containing NUL bytes (after BOM decoding, or anywhere in raw bytes)
       are treated as binary.
    3. Otherwise the encoding fallback chain is tried:
       UTF-8 -> UTF-8-SIG -> cp1251 -> latin-1 (the latter never fails).
    4. Line endings are normalized to LF (CRLF/CR -> LF), so the pack is
       byte-consistent across platforms and free of CR token noise.

    :param file_path: Path to the file to read.
    :return: Tuple (content, error_reason, encoding) where encoding is the
             codec that was actually used (None on failure). Callers can use
             it to warn when a non-UTF-8 file was transcoded.
    """
    try:
        raw = Path(file_path).read_bytes()
    except PermissionError:
        return None, "permission denied", None
    except OSError as e:
        return None, f"read error: {e}", None

    for bom, encoding in _BOM_ENCODINGS:
        if raw.startswith(bom):
            try:
                text = raw.decode(encoding)
                if "\x00" in text:
                    return None, "binary file", None
                return _normalize_newlines(text), None, encoding
            except UnicodeDecodeError:
                return None, "encoding error (invalid BOM-marked content)", None

    if looks_binary(raw):
        return None, "binary file", None

    for encoding in ENCODING_FALLBACK_CHAIN:
        try:
            return _normalize_newlines(raw.decode(encoding)), None, encoding
        except UnicodeDecodeError:
            continue

    return None, "encoding error", None
