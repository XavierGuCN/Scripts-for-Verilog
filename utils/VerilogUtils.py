#!/usr/bin/env python3
"""Reusable Verilog parsing helpers shared across scripts."""

from __future__ import annotations

import os
import re
import shlex
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from utils.PyUtils import collect_files_by_extensions


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

MODULE_RE = re.compile(
    r"\bmodule\s+([A-Za-z_][A-Za-z0-9_$]*)\s*(?:#\s*\((.*?)\))?\s*\((.*?)\)\s*;(.*?)\bendmodule\b",
    re.S,
)

INSTANCE_RE = re.compile(
    r"(?<!\bmodule\s)(?<!\bprimitive\s)(?<!\bendprimitive\s)"
    r"\b([A-Za-z_][A-Za-z0-9_$]*)\s*"
    r"(?:#\s*\((.*?)\))?\s+"
    r"([A-Za-z_][A-Za-z0-9_$]*)\s*"
    r"\((.*?)\)\s*;",
    re.S,
)

PORT_CONNECTION_RE = re.compile(r"\.\s*([A-Za-z_][A-Za-z0-9_$]*)\s*\(\s*(.*?)\s*\)", re.S)
DECL_RE = re.compile(
    r"\b(input|output|inout)\b\s*"
    r"(?:wire|reg|logic|signed|unsigned|tri|supply0|supply1|wand|wor|uwire|var|\s)*"
    r"(\[[^\]]+\])?\s*"
    r"([^;]+?)\s*;",
    re.S,
)
PORT_DECL_ITEM_RE = re.compile(
    r"^\s*(input|output|inout)\b\s*"
    r"((?:wire|reg|logic|signed|unsigned|tri|supply0|supply1|wand|wor|uwire|var|\[[^\]]+\]|\s)*)"
    r"([A-Za-z_][A-Za-z0-9_$]*)\s*$",
    re.S,
)
IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")
LITERAL_RE = re.compile(r"^\d+'[bBoOdDhH][0-9a-fA-F_xXzZ?]+$|^[0-9]+$")
SIGNAL_REF_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_$]*)(?:\s*\[[^\]]+\]\s*)*$")


class VerilogAnalysisError(Exception):
    """Raised when the script detects unsupported or invalid Verilog structure."""


@dataclass
class PortDef:
    """Normalized Verilog port definition."""

    name: str
    direction: str
    width: str = ""

    @property
    def width_clean(self) -> str:
        """Return width text without internal whitespace."""
        return normalize_width(self.width)


@dataclass
class ModuleDef:
    """Parsed Verilog module plus source-location metadata."""

    name: str
    file_path: Path
    ports: Dict[str, PortDef] = field(default_factory=dict)
    parameters: str = ""
    body: str = ""
    span: Tuple[int, int] = (0, 0)


@dataclass
class NamedConnection:
    """One named port connection from an instance, such as ``.clk(sys_clk)``."""

    port_name: str
    expr: str


@dataclass
class InstanceDef:
    """Parsed module instance with parameter overrides and named connections."""

    module_name: str
    instance_name: str
    parameter_overrides: str
    connections: List[NamedConnection]


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
        raise VerilogAnalysisError(f"Filelist does not exist: '{resolved_filelist}'")

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
                    raise VerilogAnalysisError(
                        f"Option '{token}' in filelist '{resolved_filelist}' is missing its argument."
                    )
                token_arg = tokens[index + 1]
                index += 2
            else:
                token_arg = ""
                index += 1

            if token in ("-f", "-F"):
                nested_arg = token_arg
                nested_filelist = _resolve_path_reference(nested_arg, base_dir)
                _collect_from_filelist(
                    nested_filelist,
                    extensions,
                    source_files,
                    library_roots,
                    visited_filelists,
                )
                continue

            if token == "-v":
                source_arg = token_arg
                source_path = _resolve_path_reference(source_arg, base_dir)
                if not source_path.is_file():
                    raise VerilogAnalysisError(
                        f"Source file '{source_path}' referenced by filelist '{resolved_filelist}' was not found."
                    )
                _add_source_file_if_match(source_path, extensions, source_files)
                continue

            if token == "-y":
                root_arg = token_arg
                root_path = _resolve_path_reference(root_arg, base_dir)
                if not root_path.is_dir():
                    raise VerilogAnalysisError(
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
            raise VerilogAnalysisError(f"Search root is not a directory: '{search_root}'")
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


def normalize_width(width: str) -> str:
    """Normalize a width expression by removing whitespace."""
    return re.sub(r"\s+", "", width or "")


def split_top_level_csv(text: str) -> List[str]:
    """Split a comma-separated list while ignoring commas inside (), [] and {}."""
    items: List[str] = []
    current: List[str] = []
    paren_depth = 0
    bracket_depth = 0
    brace_depth = 0

    for char in text:
        # Only split on commas that live at the current top parsing level.
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


def normalize_decl_names(names_text: str) -> List[str]:
    """Extract declared signal names and strip default assignments plus packed dimensions."""
    names = []
    for raw_name in split_top_level_csv(names_text):
        name = raw_name.strip()
        if "=" in name:
            name = name.split("=", 1)[0].strip()
        name = re.sub(r"\[[^\]]+\]", "", name).strip()
        if not name:
            continue
        if not IDENT_RE.match(name):
            raise VerilogAnalysisError(f"Unsupported declared port name: {raw_name}")
        names.append(name)
    return names


def parse_header_ports(header_text: str) -> Dict[str, PortDef]:
    """Parse ANSI-style ports from a module header."""
    ports: Dict[str, PortDef] = {}
    for item in split_top_level_csv(header_text):
        match = PORT_DECL_ITEM_RE.match(item)
        if not match:
            continue
        direction, qualifiers, name = match.groups()
        width_match = re.search(r"\[[^\]]+\]", qualifiers or "")
        ports[name] = PortDef(name=name, direction=direction, width=width_match.group(0) if width_match else "")
    return ports


def parse_body_ports(body_text: str) -> Dict[str, PortDef]:
    """Parse non-ANSI port declarations from a module body."""
    ports: Dict[str, PortDef] = {}
    for direction, width, names_text in DECL_RE.findall(body_text):
        for name in normalize_decl_names(names_text):
            ports[name] = PortDef(name=name, direction=direction, width=width or "")
    return ports


def parse_named_port_connections(connections_text: str) -> List[NamedConnection]:
    """Parse named connections from an instance connection list."""
    return [
        NamedConnection(port_name=port_name, expr=expr)
        for port_name, expr in PORT_CONNECTION_RE.findall(connections_text)
    ]


def parse_module_instances(module_body: str) -> List[InstanceDef]:
    """Parse module instances from a module body, ignoring commented-out instances and ports."""
    instances: List[InstanceDef] = []
    body_without_comments = strip_comments_preserve_layout(module_body)
    for match in INSTANCE_RE.finditer(body_without_comments):
        instances.append(
            InstanceDef(
                module_name=match.group(1),
                parameter_overrides=(match.group(2) or "").strip(),
                instance_name=match.group(3),
                connections=parse_named_port_connections(match.group(4)),
            )
        )
    return instances


def parse_modules_from_file(file_path: Path) -> Dict[str, ModuleDef]:
    """Parse every module definition from one Verilog source file."""
    original_text = file_path.read_text(encoding="utf-8")
    text = strip_comments_preserve_layout(original_text)
    modules: Dict[str, ModuleDef] = {}
    for match in MODULE_RE.finditer(text):
        module_name = match.group(1)
        header_ports = strip_comments(match.group(3))
        body = original_text[match.start(4) : match.end(4)]
        body_clean = strip_comments(match.group(4))
        parameters = original_text[match.start(2) : match.end(2)] if match.group(2) else ""
        port_map = parse_body_ports(body_clean)
        port_map.update(parse_header_ports(header_ports))
        modules[module_name] = ModuleDef(
            name=module_name,
            file_path=file_path,
            ports=port_map,
            parameters=parameters.strip(),
            body=body,
            span=(match.start(), match.end()),
        )
    return modules


def load_module_library(
    search_roots: Path | Sequence[Path] | None,
    extensions: Sequence[str],
    filelists: Path | Sequence[Path] | None = None,
) -> Dict[str, ModuleDef]:
    """Load modules from search roots and recursively expanded filelists, rejecting duplicates."""
    module_defs: Dict[str, ModuleDef] = {}
    for file_path in collect_verilog_source_files(search_roots, extensions, filelists=filelists):
        for module_name, module_def in parse_modules_from_file(file_path).items():
            if module_name in module_defs:
                raise VerilogAnalysisError(
                    f"Module '{module_name}' is defined in both '{module_defs[module_name].file_path}' and '{file_path}'"
                )
            module_defs[module_name] = module_def
    return module_defs


def get_target_top_module(top_file: Path, top_module_name: Optional[str]) -> ModuleDef:
    """Return the requested top module, or the only module found in ``top_file``."""
    modules = parse_modules_from_file(top_file)
    if not modules:
        raise VerilogAnalysisError(f"No module definition found in '{top_file}'")
    if top_module_name:
        if top_module_name not in modules:
            raise VerilogAnalysisError(f"Top module '{top_module_name}' not found in '{top_file}'")
        return modules[top_module_name]
    if len(modules) > 1:
        raise VerilogAnalysisError(
            f"Multiple modules found in '{top_file}'. Please specify --top-module. Candidates: {', '.join(sorted(modules))}"
        )
    return next(iter(modules.values()))


def extract_base_signal_name(expr: str) -> Optional[str]:
    """Convert a simple connection expression to its base signal name.

    Returns ``None`` for literal constants. Expressions like ``cfg[2:0]`` are
    normalized to ``cfg`` so higher-level tools can reason about one logical net.
    """
    expr = expr.strip()
    if not expr:
        return None
    if LITERAL_RE.match(expr):
        return None
    signal_match = SIGNAL_REF_RE.match(expr)
    if signal_match:
        return signal_match.group(1)
    if IDENT_RE.match(expr):
        return expr
    raise VerilogAnalysisError(
        f"Unsupported connection expression '{expr}'. Only simple net names or constants are supported."
    )


def format_port_decl(direction: str, width: str, signal_name: str) -> str:
    """Format a Verilog port declaration line."""
    if width:
        return f"{direction} {width} {signal_name}"
    return f"{direction} {signal_name}"
