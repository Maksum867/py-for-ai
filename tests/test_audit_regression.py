"""Independent audit regressions, kept outside the repository."""

import ast
import json
import subprocess
import sys

import pytest

from py_ai.cli import parse_size, parse_token_budget
from py_ai.core import pack_project
from py_ai.filters import should_ignore
from py_ai.stripping import _collect_and_remove_docstrings, _fallback_strip


def pack(root, output, **kwargs):
    pack_project(root, output, copy_to_clipboard=False, output_format="json", **kwargs)
    return json.loads(output.read_text(encoding="utf-8"))


def link(target, name, is_dir=False):
    try:
        name.symlink_to(target, target_is_directory=is_dir)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlinks unavailable: {exc}")


@pytest.mark.parametrize("pattern", ["secret.txt", "*.txt", "private/*", "private/"])
def test_exclude_cannot_be_bypassed_by_alias(tmp_path, pattern):
    root = tmp_path / "project"
    private = root / "private"
    private.mkdir(parents=True)
    target = private / "secret.txt"
    target.write_text("secret", encoding="utf-8")
    link(target, root / "alias.py")
    result = pack(root, tmp_path / "out.json", exclude_patterns=[pattern])
    assert not result["files"]


def test_ignore_cannot_be_bypassed_by_alias(tmp_path):
    pytest.importorskip("pathspec")
    root = tmp_path / "project"
    root.mkdir()
    (root / ".gitignore").write_text("secret.txt\n", encoding="utf-8")
    (root / "secret.txt").write_text("secret", encoding="utf-8")
    link("secret.txt", root / "alias.py")
    result = pack(root, tmp_path / "out.json")
    assert "alias.py" not in [f["path"] for f in result["files"]]


@pytest.mark.parametrize("pattern", ["src*/a.py", "src?/a.py", "src[1]/a.py"])
def test_include_wildcard_directory(tmp_path, pattern):
    root = tmp_path / "project"
    (root / "src1").mkdir(parents=True)
    (root / "src1" / "a.py").write_text("x=1", encoding="utf-8")
    result = pack(root, tmp_path / "out.json", include_patterns=[pattern])
    assert [f["path"] for f in result["files"]] == ["src1/a.py"]


def test_aliases_deduplicated_when_real_file_not_included(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    (root / "original.txt").write_text("hello", encoding="utf-8")
    link("original.txt", root / "a.py")
    link("original.txt", root / "b.py")
    result = pack(root, tmp_path / "out.json", include_patterns=["*.py"])
    assert len(result["files"]) == 1
    assert len(result["skipped"]) == 1


def test_public_ignore_detects_external_parent_link(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "value.txt").write_text("x", encoding="utf-8")
    link(outside, root / "link", is_dir=True)
    assert should_ignore(root / "link" / "value.txt", root)


@pytest.mark.parametrize(
    "source",
    [
        'def f(): "docs"; return 3\n',
        'def f():\n    """doc"""\n    return 3\n',
        'class C:\n    """doc"""\n',
        '"""module"""\nfrom __future__ import annotations\nx = 2\n',
        'x = """one\n\n\ntwo"""\n',
        'x = "https://example/#fragment"  # comment\n',
        'def f():\n    """документація"""\n    return "ї"\n',
    ],
)
def test_fallback_preserves_runtime_values(source):
    tree = ast.parse(source)
    result = _fallback_strip(source, _collect_and_remove_docstrings(tree))
    original, stripped = {}, {}
    exec(compile(source, "source", "exec"), original)  # noqa: S102
    exec(compile(result, "stripped", "exec"), stripped)  # noqa: S102
    if "f" in original:
        assert original["f"]() == stripped["f"]()
    if "x" in original:
        assert original["x"] == stripped["x"]
    if "C" in original:
        assert "C" in stripped


@pytest.mark.parametrize("parser", [parse_size, parse_token_budget])
@pytest.mark.parametrize("value", ["9" * 400, "0", "-1", "abc"])
def test_invalid_numeric_input_is_value_error(parser, value):
    with pytest.raises(ValueError):
        parser(value)


@pytest.mark.parametrize("flag", ["--budget", "--max-file-size"])
def test_cli_huge_value_has_no_traceback(tmp_path, flag):
    result = subprocess.run(
        [sys.executable, "-m", "py_ai", str(tmp_path), flag, "9" * 400],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
    assert "Traceback" not in result.stderr


@pytest.mark.parametrize("base", ["src", "pkg", "module"])
@pytest.mark.parametrize("suffix", ["1", "2", "a"])
@pytest.mark.parametrize("extension", ["py", "txt", "md"])
def test_include_glob_directory_matrix(tmp_path, base, suffix, extension):
    root = tmp_path / "project"
    directory = root / (base + suffix)
    directory.mkdir(parents=True)
    (directory / ("item." + extension)).write_text("data", encoding="utf-8")
    result = pack(root, tmp_path / "out.json", include_patterns=[base + "*/item." + extension])
    assert [entry["path"] for entry in result["files"]] == [base + suffix + "/item." + extension]


@pytest.mark.parametrize(
    "value,expected", [("1", 1), ("1KB", 1024), ("1.5MB", 1572864), ("2gb", 2147483648)]
)
def test_size_normal_values(value, expected):
    assert parse_size(value) == expected


@pytest.mark.parametrize("value,expected", [("1", 1), ("1k", 1000), ("1.5m", 1500000)])
def test_budget_normal_values(value, expected):
    assert parse_token_budget(value) == expected


def test_hidden_symlink_name_is_not_packed(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    (root / "visible.txt").write_text("secret", encoding="utf-8")
    link("visible.txt", root / ".hidden")
    result = pack(root, tmp_path / "out.json", include_patterns=[".hidden"])
    assert not result["files"]


@pytest.mark.parametrize("subdir", [False, True])
def test_gitignore_escaped_trailing_space(tmp_path, subdir):
    pytest.importorskip("pathspec")
    if sys.platform == "win32":
        pytest.skip("Windows cannot create filenames ending with a space")
    root = tmp_path / "project"
    directory = root / "sub" if subdir else root
    directory.mkdir(parents=True)
    (directory / ".gitignore").write_text("secret\\ \n", encoding="utf-8")
    (directory / "secret ").write_text("secret", encoding="utf-8")
    result = pack(root, tmp_path / "out.json")
    assert all(not entry["path"].endswith("secret ") for entry in result["files"])


def test_nested_ignore_directory_name_with_glob_chars(tmp_path):
    pytest.importorskip("pathspec")
    root = tmp_path / "project"
    directory = root / "[pkg]"
    directory.mkdir(parents=True)
    (directory / ".gitignore").write_text("secret.txt\n", encoding="utf-8")
    (directory / "secret.txt").write_text("secret", encoding="utf-8")
    result = pack(root, tmp_path / "out.json")
    assert "[pkg]/secret.txt" not in [entry["path"] for entry in result["files"]]


def test_ignore_file_symlink_not_followed(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    (root / "data.txt").write_text("hello", encoding="utf-8")
    outside = tmp_path / "external-ignore"
    outside.write_text("data.txt\n", encoding="utf-8")
    link(outside, root / ".gitignore")
    result = pack(root, tmp_path / "out.json")
    assert "data.txt" in [entry["path"] for entry in result["files"]]


def test_failed_output_write_preserves_existing_file(tmp_path, monkeypatch):
    root = tmp_path / "project"
    root.mkdir()
    (root / "input.txt").write_text("input", encoding="utf-8")
    output = tmp_path / "output.txt"
    output.write_text("old output", encoding="utf-8")

    def failed_replace(*args, **kwargs):
        raise OSError("simulated disk failure")

    monkeypatch.setattr("py_ai.core.os.replace", failed_replace)
    with pytest.raises(OSError):
        pack_project(root, output, copy_to_clipboard=False)
    assert output.read_text(encoding="utf-8") == "old output"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["output.txt", "project"]


def test_output_hardlink_does_not_overwrite_input(tmp_path):
    import os

    root = tmp_path / "project"
    root.mkdir()
    source = root / "input.txt"
    source.write_text("original", encoding="utf-8")
    output = tmp_path / "output.txt"
    try:
        os.link(source, output)
    except OSError as exc:
        pytest.skip(f"hardlinks unavailable: {exc}")
    pack_project(root, output, copy_to_clipboard=False)
    assert source.read_text(encoding="utf-8") == "original"


@pytest.mark.parametrize("parser", [parse_size, parse_token_budget])
def test_integer_input_is_exact_above_float_precision(parser):
    assert parser("9007199254740993") == 9007199254740993


def test_api_rejects_nonpositive_max_file_size(tmp_path):
    with pytest.raises(ValueError):
        pack_project(tmp_path, tmp_path / "out", copy_to_clipboard=False, max_file_size=0)


@pytest.mark.parametrize("encoding", ["utf-8-sig", "utf-16", "utf-32"])
def test_bom_text_with_embedded_nul_is_binary(tmp_path, encoding):
    from py_ai.readers import read_text_content

    path = tmp_path / "file.txt"
    path.write_bytes("hello\0world".encode(encoding))
    content, reason, _ = read_text_content(path)
    assert content is None
    assert reason == "binary file"


def test_case_tie_order_does_not_depend_on_creation_order(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    (root / "a.py").write_text("lower", encoding="utf-8")
    (root / "A.py").write_text("upper", encoding="utf-8")
    if len(list(root.iterdir())) != 2:
        pytest.skip("case-insensitive filesystem")
    result = pack(root, tmp_path / "out.json")
    assert [entry["path"] for entry in result["files"]] == ["A.py", "a.py"]


def test_quiet_clipboard_failure_reports_warning(tmp_path, monkeypatch, capsys):
    import pyperclip

    from py_ai.cli import main

    def fail_copy(_text):
        raise pyperclip.PyperclipException("clipboard unavailable")

    monkeypatch.setattr(pyperclip, "copy", fail_copy)
    monkeypatch.setattr(
        sys, "argv", ["py-for-ai", str(tmp_path), "--quiet", "-o", str(tmp_path / "out")]
    )
    main()
    captured = capsys.readouterr()
    assert not captured.out
    assert "Could not copy to clipboard" in captured.err
    assert captured.err.count("Could not copy to clipboard") == 1


def test_include_bare_directory_name(tmp_path):
    root = tmp_path / "project"
    (root / "src" / "nested").mkdir(parents=True)
    (root / "src" / "nested" / "main.py").write_text("x=1", encoding="utf-8")
    (root / "unrelated.py").write_text("x=2", encoding="utf-8")
    result = pack(root, tmp_path / "out.json", include_patterns=["src"])
    assert [entry["path"] for entry in result["files"]] == ["src/nested/main.py"]


def test_exclude_patterns_generator_applies_to_every_file(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    (root / "first.py").write_text("first", encoding="utf-8")
    (root / "second.py").write_text("second", encoding="utf-8")
    result = pack(root, tmp_path / "out.json", exclude_patterns=iter(["*.py"]))
    assert result["files"] == []


def test_new_brand_command_name(monkeypatch):
    from py_ai.cli import _program_name

    monkeypatch.setattr(sys, "argv", ["/usr/bin/py-for-ai"])
    assert _program_name() == "py-for-ai"


@pytest.mark.parametrize("pattern", ["src*/", "src*/**", "src?/", "src[1]/**"])
def test_include_glob_directory_itself(tmp_path, pattern):
    root = tmp_path / "project"
    (root / "src1" / "nested").mkdir(parents=True)
    (root / "src1" / "nested" / "main.py").write_text("x=1", encoding="utf-8")
    result = pack(root, tmp_path / "out.json", include_patterns=[pattern])
    assert [entry["path"] for entry in result["files"]] == ["src1/nested/main.py"]
