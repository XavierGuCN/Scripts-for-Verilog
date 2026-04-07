#!/usr/bin/env python3
"""Generic Python helpers shared across local scripts."""

from __future__ import annotations

from pathlib import Path
from typing import List, Sequence, Tuple


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


def collect_files_by_extensions(search_root: Path, extensions: Sequence[str]) -> List[Path]:
    """Recursively collect files under ``search_root`` whose suffix matches ``extensions``."""
    file_paths = set()
    for ext in extensions:
        file_paths.update(search_root.glob(f"**/*{ext}"))
    return sorted(path for path in file_paths if path.is_file())
