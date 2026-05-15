#!/usr/bin/env python3
"""Generic Python helpers shared across local scripts."""

from __future__ import annotations

from pathlib import Path
from typing import List, Sequence


def collect_files_by_extensions(search_root: Path, extensions: Sequence[str]) -> List[Path]:
    """Recursively collect files under ``search_root`` whose suffix matches ``extensions``."""
    file_paths = set()
    for ext in extensions:
        file_paths.update(search_root.glob(f"**/*{ext}"))
    return sorted(path for path in file_paths if path.is_file())
