#!/usr/bin/env python3
"""Reusable Verilog-aware text and source-file helpers shared across scripts."""

from __future__ import annotations

import os
import re
import shlex
from pathlib import Path
from typing import Any, List, Sequence, Tuple

from utils.PyUtils import collect_files_by_extensions


__all__ = [
    "DEFAULT_VERILOG_EXTENSIONS",
    "FILELIST_IGNORE_OPTIONS_WITH_ARG",
    "VerilogTextError",
    "collect_verilog_source_files",
    "ensure_trailing_newline",
    "find_matching_paren",
    "has_trailing_comma",
    "indent_block",
    "infer_port_indent",
    "iter_line_spans",
    "replace_span",
    "set_trailing_comma",
    "skip_whitespace",
    "split_line_ending",
    "split_top_level_csv",
    "strip_comments",
    "strip_comments_preserve_layout",
]


DEFAULT_VERILOG_EXTENSIONS = (".v", ".sv", ".vh", ".svh")
FILELIST_IGNORE_OPTIONS_WITH_ARG = {
    "-D",
    "-I",
    "-L",
    "-P",
    "-U",
    "-include",
    "-incdir",
    "-libext",
    "-l",
    "-timescale",
    "-work",
}


class VerilogTextError(Exception):
    """Raised when source text or source-file collection cannot be processed."""


def indent_block(text: str, indent: str = "    ") -> str:
    """Indent every non-empty line in a multi-line string."""
    return "\n".join(f"{indent}{line}" if line.strip() else "" for line in text.splitlines())


def ensure_trailing_newline(text: str) -> str:
    """Ensure text ends with a newline character."""
    if text.endswith("\n"):
        return text
    return text + "\n"


def replace_span(text: str, span: Tuple[int, int], replacement: str) -> str:
    """Replace a half-open character span ``[start, end)`` inside ``text``."""
    start, end = span
    return text[:start] + replacement + text[end:]


def _normalize_extensions(extensions: Sequence[str]) -> Tuple[str, ...]:
    return tuple(ext.lower() for ext in extensions)


def _coerce_path_sequence(paths: Path | Sequence[Path] | None) -> List[Path]:
    if paths is None:
        return []
    if isinstance(paths, Path):
        return [paths]
    return list(paths)


def _resolve_path_reference(raw_path: str, base_dir: Path) -> Path:
    expanded = Path(os.path.expanduser(os.path.expandvars(raw_path)))
    if not expanded.is_absolute():
        expanded = base_dir / expanded
    return expanded.resolve()


def _add_source_file_if_match(file_path: Path, extensions: Sequence[str], source_files: set[Path]) -> None:
    if file_path.is_file() and file_path.suffix.lower() in extensions:
        source_files.add(file_path)


def _tokenize_filelist_line(line: str) -> List[str]:
    line = re.sub(r"//.*$", "", line)
    line = re.sub(r"#.*$", "", line)
    stripped = line.strip()
    if not stripped:
        return []
    return shlex.split(stripped, posix=True)


def skip_whitespace(text: str, index: int) -> int:
    """Return the first index at or after ``index`` that is not whitespace."""
    while index < len(text) and text[index].isspace():
        index += 1
    return index


def _collect_from_filelist(
    filelist_path: Path,
    extensions: Sequence[str],
    source_files: set[Path],
    library_roots: set[Path],
    visited_filelists: set[Path],
) -> None:
    resolved_filelist = filelist_path.resolve()
    if resolved_filelist in visited_filelists:
        return
    if not resolved_filelist.is_file():
        raise VerilogTextError(f"Filelist does not exist: '{resolved_filelist}'")

    visited_filelists.add(resolved_filelist)
    base_dir = resolved_filelist.parent

    for raw_line in resolved_filelist.read_text(encoding="utf-8").splitlines():
        tokens = _tokenize_filelist_line(raw_line)
        index = 0
        while index < len(tokens):
            token = tokens[index]

            if token in FILELIST_IGNORE_OPTIONS_WITH_ARG:
                if index + 1 < len(tokens):
                    index += 2
                else:
                    index += 1
                continue

            if token in ("-f", "-F", "-v", "-y"):
                if index + 1 >= len(tokens):
                    raise VerilogTextError(
                        f"Option '{token}' in filelist '{resolved_filelist}' is missing its argument."
                    )
                token_arg = tokens[index + 1]
                index += 2
            else:
                token_arg = ""
                index += 1

            if token in ("-f", "-F"):
                nested_filelist = _resolve_path_reference(token_arg, base_dir)
                _collect_from_filelist(
                    nested_filelist,
                    extensions,
                    source_files,
                    library_roots,
                    visited_filelists,
                )
                continue

            if token == "-v":
                source_path = _resolve_path_reference(token_arg, base_dir)
                if not source_path.is_file():
                    raise VerilogTextError(
                        f"Source file '{source_path}' referenced by filelist '{resolved_filelist}' was not found."
                    )
                _add_source_file_if_match(source_path, extensions, source_files)
                continue

            if token == "-y":
                root_path = _resolve_path_reference(token_arg, base_dir)
                if not root_path.is_dir():
                    raise VerilogTextError(
                        f"Library directory '{root_path}' referenced by filelist '{resolved_filelist}' was not found."
                    )
                library_roots.add(root_path)
                continue

            if token.startswith("+"):
                continue

            if token.startswith("-"):
                continue

            candidate_path = _resolve_path_reference(token, base_dir)
            if candidate_path.is_file():
                _add_source_file_if_match(candidate_path, extensions, source_files)
            elif candidate_path.is_dir():
                library_roots.add(candidate_path)


def collect_verilog_source_files(
    search_roots: Path | Sequence[Path] | None,
    extensions: Sequence[str],
    filelists: Path | Sequence[Path] | None = None,
) -> List[Path]:
    """Collect Verilog source files from search roots plus recursively expanded filelists."""
    normalized_extensions = _normalize_extensions(extensions)
    resolved_roots = {path.resolve() for path in _coerce_path_sequence(search_roots)}
    resolved_filelists = [path.resolve() for path in _coerce_path_sequence(filelists)]

    source_files: set[Path] = set()
    visited_filelists: set[Path] = set()

    for filelist_path in resolved_filelists:
        _collect_from_filelist(
            filelist_path,
            normalized_extensions,
            source_files,
            resolved_roots,
            visited_filelists,
        )

    for search_root in sorted(resolved_roots):
        if not search_root.is_dir():
            raise VerilogTextError(f"Search root is not a directory: '{search_root}'")
        source_files.update(collect_files_by_extensions(search_root, normalized_extensions))

    return sorted(source_files)


def strip_comments(text: str) -> str:
    """Remove line and block comments from Verilog text."""
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    text = re.sub(r"//.*?$", "", text, flags=re.M)
    return text


def strip_comments_preserve_layout(text: str) -> str:
    """Blank out comments while preserving line/column layout for regex-based parsing."""

    def replace_block(match: re.Match[str]) -> str:
        return re.sub(r"[^\n]", " ", match.group(0))

    def replace_line(match: re.Match[str]) -> str:
        return re.sub(r"[^\n]", " ", match.group(0))

    text = re.sub(r"/\*.*?\*/", replace_block, text, flags=re.S)
    text = re.sub(r"//.*?$", replace_line, text, flags=re.M)
    return text


def split_top_level_csv(text: str) -> List[str]:
    """Split a comma-separated list while ignoring commas inside (), [] and {}."""
    items: List[str] = []
    current: List[str] = []
    paren_depth = 0
    bracket_depth = 0
    brace_depth = 0

    for char in text:
        if char == "," and paren_depth == 0 and bracket_depth == 0 and brace_depth == 0:
            item = "".join(current).strip()
            if item:
                items.append(item)
            current = []
            continue
        current.append(char)
        if char == "(":
            paren_depth += 1
        elif char == ")":
            paren_depth = max(paren_depth - 1, 0)
        elif char == "[":
            bracket_depth += 1
        elif char == "]":
            bracket_depth = max(bracket_depth - 1, 0)
        elif char == "{":
            brace_depth += 1
        elif char == "}":
            brace_depth = max(brace_depth - 1, 0)

    tail = "".join(current).strip()
    if tail:
        items.append(tail)
    return items


def find_matching_paren(text: str, open_index: int) -> int:
    """Return the index of the matching ')' for the '(' at ``open_index``."""
    depth = 0
    for index in range(open_index, len(text)):
        char = text[index]
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return index
    raise VerilogTextError("Could not find matching ')' in text.")


def iter_line_spans(text: str, start: int, end: int) -> List[Tuple[int, int, str]]:
    """Return source lines and their half-open spans within ``text[start:end]``."""
    lines: List[Tuple[int, int, str]] = []
    index = start
    while index < end:
        newline_index = text.find("\n", index, end)
        line_end = end if newline_index == -1 else newline_index + 1
        lines.append((index, line_end, text[index:line_end]))
        index = line_end
    return lines


def split_line_ending(line_text: str) -> Tuple[str, str]:
    """Split a line into content and its newline sequence."""
    if line_text.endswith("\r\n"):
        return line_text[:-2], "\r\n"
    if line_text.endswith("\n"):
        return line_text[:-1], "\n"
    return line_text, ""


def has_trailing_comma(line_text: str) -> bool:
    """Return whether the non-comment code portion of a line ends with a comma."""
    clean_line = strip_comments(line_text).rstrip()
    return clean_line.endswith(",")


def set_trailing_comma(line_text: str, should_have_comma: bool) -> str:
    """Add or remove a trailing comma from a line while preserving line comments."""
    if has_trailing_comma(line_text) == should_have_comma:
        return line_text

    body, newline = split_line_ending(line_text)
    comment_index = body.find("//")
    code = body if comment_index == -1 else body[:comment_index]
    comment = "" if comment_index == -1 else body[comment_index:]

    if should_have_comma:
        updated_code = code.rstrip() + ","
        spacing = "" if not comment else " "
        return f"{updated_code}{spacing}{comment}{newline}"

    updated_code = re.sub(r",\s*$", "", code.rstrip())
    spacing = "" if not comment else " "
    return f"{updated_code}{spacing}{comment}{newline}"


def infer_port_indent(existing_ports: Sequence[Any], source_text: str, content_start: int) -> str:
    """Infer indentation for new header ports from existing port lines or module indentation."""
    for port in existing_ports:
        indent_match = re.match(r"^(\s*)", port.text)
        if indent_match and port.text.strip():
            return indent_match.group(1)

    line_start = source_text.rfind("\n", 0, content_start)
    module_indent = ""
    if line_start != -1:
        header_line = source_text[line_start + 1 : content_start]
        module_indent_match = re.match(r"^(\s*)", header_line)
        module_indent = module_indent_match.group(1) if module_indent_match else ""
    return module_indent + "    "
