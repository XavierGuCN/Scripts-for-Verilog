#!/usr/bin/env python3
"""Reusable Verilog syntax parsing and signal-analysis helpers shared across scripts."""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Set, Tuple

from utils.VerilogTextUtils import (
    VerilogTextError,
    collect_verilog_source_files,
    find_matching_paren,
    iter_line_spans,
    skip_whitespace,
    split_top_level_csv,
    strip_comments,
    strip_comments_preserve_layout,
)


__all__ = [
    "DECL_RE",
    "IDENT_RE",
    "INSTANCE_RE",
    "INTERNAL_SIGNAL_DECL_RE",
    "LITERAL_RE",
    "MODULE_RE",
    "PORT_CONNECTION_RE",
    "PORT_DECL_ITEM_RE",
    "SIGNAL_REF_RE",
    "HeaderPortLine",
    "InstanceDef",
    "InstancePortRef",
    "ModuleDef",
    "NamedConnection",
    "PortDef",
    "SignalAnalysis",
    "VerilogAnalysisError",
    "analyze_signals",
    "ensure_same_width",
    "extract_base_signal_name",
    "format_port_decl",
    "get_target_top_module",
    "infer_default_top_direction",
    "load_module_library",
    "locate_header_port_list",
    "normalize_decl_names",
    "normalize_width",
    "parse_body_ports",
    "parse_header_port_lines",
    "parse_header_port_name",
    "parse_header_ports",
    "parse_instances",
    "parse_internal_signal_names",
    "parse_module_instances",
    "parse_modules_from_file",
    "parse_named_port_connections",
]


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
INTERNAL_SIGNAL_DECL_RE = re.compile(
    r"\b(?:wire|reg|logic|tri|wand|wor|uwire)\b\s*"
    r"(?:signed|unsigned|\s)*"
    r"(\[[^\]]+\])?\s*"
    r"([^;]+?)\s*;",
    re.S,
)


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


@dataclass
class HeaderPortLine:
    """One source line inside a module header port list."""

    name: str
    start: int
    end: int
    text: str


@dataclass
class InstancePortRef:
    """One resolved instance port reference connected to a top-level signal."""

    instance_name: str
    module_name: str
    port_name: str
    signal_name: str
    direction: str
    width: str


@dataclass
class SignalAnalysis:
    """Direction and width analysis result for one top-level signal."""

    signal_name: str
    top_direction: Optional[str]
    width: str
    reason: str
    refs: List[InstancePortRef]


def normalize_width(width: str) -> str:
    """Normalize a width expression by removing whitespace."""
    return re.sub(r"\s+", "", width or "")


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
    try:
        source_files = collect_verilog_source_files(search_roots, extensions, filelists=filelists)
    except VerilogTextError as exc:
        raise VerilogAnalysisError(str(exc)) from exc

    for file_path in source_files:
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


def parse_instances(top_module: ModuleDef, module_library: Dict[str, ModuleDef]) -> List[InstancePortRef]:
    """Resolve named instance port connections in ``top_module`` against ``module_library``."""
    refs: List[InstancePortRef] = []
    for instance in parse_module_instances(top_module.body):
        submodule_name = instance.module_name
        instance_name = instance.instance_name

        if submodule_name not in module_library:
            raise VerilogAnalysisError(
                f"Definition for instantiated module '{submodule_name}' (instance '{instance_name}') was not found."
            )
        submodule_def = module_library[submodule_name]

        for connection in instance.connections:
            port_name = connection.port_name
            if port_name not in submodule_def.ports:
                raise VerilogAnalysisError(
                    f"Port '{port_name}' on instance '{instance_name}' was not found in module '{submodule_name}'."
                )
            signal_name = extract_base_signal_name(connection.expr)
            if signal_name is None:
                continue
            port_def = submodule_def.ports[port_name]
            refs.append(
                InstancePortRef(
                    instance_name=instance_name,
                    module_name=submodule_name,
                    port_name=port_name,
                    signal_name=signal_name,
                    direction=port_def.direction,
                    width=port_def.width_clean,
                )
            )
    return refs


def ensure_same_width(signal_name: str, refs: Sequence[InstancePortRef]) -> str:
    """Return the common width for ``signal_name``, or raise if connected widths differ."""
    widths = {ref.width for ref in refs}
    if len(widths) > 1:
        detail = ", ".join(
            f"{ref.instance_name}.{ref.port_name}={ref.width or 'scalar'}" for ref in refs
        )
        raise VerilogAnalysisError(f"Width mismatch detected on signal '{signal_name}': {detail}")
    return next(iter(widths), "")


def parse_internal_signal_names(top_module: ModuleDef) -> Set[str]:
    """Parse signal names declared internally in a module body."""
    declared_signals: Set[str] = set()
    body_clean = strip_comments(top_module.body)
    for _width, names_text in INTERNAL_SIGNAL_DECL_RE.findall(body_clean):
        declared_signals.update(normalize_decl_names(names_text))
    return declared_signals


def infer_default_top_direction(signal_name: str, refs: Sequence[InstancePortRef]) -> Tuple[Optional[str], str]:
    """Infer a top-level direction for one signal from connected instance port directions."""
    directions = {ref.direction for ref in refs}

    if len(refs) == 1:
        return refs[0].direction, "single connection"

    if directions == {"input"}:
        return "input", "common input"

    if directions == {"output"}:
        detail = ", ".join(f"{ref.instance_name}.{ref.port_name}" for ref in refs)
        raise VerilogAnalysisError(f"Signal '{signal_name}' is driven by multiple output ports only: {detail}")

    if directions == {"inout"} or "inout" in directions:
        return "inout", "contains inout connection"

    if directions.issubset({"input", "output"}):
        return None, "internal connection with both input and output endpoints"

    raise VerilogAnalysisError(
        f"Unsupported direction combination on signal '{signal_name}': {', '.join(sorted(directions))}"
    )


def analyze_signals(
    refs: Sequence[InstancePortRef],
    internal_signal_names: Set[str],
    forced_output_specs: Set[Tuple[str, str]],
) -> Tuple[List[SignalAnalysis], List[SignalAnalysis]]:
    """Classify connected signals as top-level ports or internal signals."""
    by_signal: Dict[str, List[InstancePortRef]] = defaultdict(list)
    for ref in refs:
        by_signal[ref.signal_name].append(ref)

    top_ports_by_name: Dict[str, SignalAnalysis] = {}
    internal_signals: List[SignalAnalysis] = []

    for signal_name in sorted(by_signal):
        signal_refs = by_signal[signal_name]
        width = ensure_same_width(signal_name, signal_refs)
        default_direction, default_reason = infer_default_top_direction(signal_name, signal_refs)
        forced_refs = [
            ref for ref in signal_refs if (ref.module_name, ref.port_name) in forced_output_specs
        ]

        if forced_refs:
            directions = {ref.direction for ref in signal_refs}
            if "inout" in directions:
                raise VerilogAnalysisError(
                    f"Signal '{signal_name}' cannot be forced to output because it also connects to inout ports."
                )
            top_ports_by_name[signal_name] = SignalAnalysis(
                signal_name=signal_name,
                top_direction="output",
                width=width,
                reason="forced as top output via force-output list",
                refs=signal_refs,
            )
            continue

        if signal_name in internal_signal_names:
            internal_signals.append(
                SignalAnalysis(
                    signal_name=signal_name,
                    top_direction=None,
                    width=width,
                    reason="kept internal because it is already declared in the top module",
                    refs=signal_refs,
                )
            )
            continue

        if default_direction is None:
            internal_signals.append(
                SignalAnalysis(
                    signal_name=signal_name,
                    top_direction=None,
                    width=width,
                    reason=default_reason,
                    refs=signal_refs,
                )
            )
            continue

        top_ports_by_name[signal_name] = SignalAnalysis(
            signal_name=signal_name,
            top_direction=default_direction,
            width=width,
            reason=default_reason,
            refs=signal_refs,
        )

    return [top_ports_by_name[name] for name in sorted(top_ports_by_name)], internal_signals


def locate_header_port_list(source_text: str, module_def: ModuleDef) -> Tuple[int, int]:
    """Locate the half-open source span containing a module header's port list contents."""
    clean_text = strip_comments_preserve_layout(source_text)
    module_start, module_end = module_def.span
    module_text = clean_text[module_start:module_end]
    module_match = re.search(r"\bmodule\s+" + re.escape(module_def.name) + r"\b", module_text)
    if not module_match:
        raise VerilogAnalysisError(f"Could not locate module header for '{module_def.name}'.")

    index = module_start + module_match.end()
    index = skip_whitespace(clean_text, index)
    if index < module_end and clean_text[index] == "#":
        index = skip_whitespace(clean_text, index + 1)
        if index >= module_end or clean_text[index] != "(":
            raise VerilogAnalysisError(f"Unsupported parameter list syntax in module '{module_def.name}'.")
        try:
            index = find_matching_paren(clean_text, index) + 1
        except VerilogTextError as exc:
            raise VerilogAnalysisError(str(exc)) from exc
        index = skip_whitespace(clean_text, index)

    if index >= module_end or clean_text[index] != "(":
        raise VerilogAnalysisError(f"Could not locate port list in module '{module_def.name}'.")

    try:
        close_index = find_matching_paren(clean_text, index)
    except VerilogTextError as exc:
        raise VerilogAnalysisError(str(exc)) from exc
    return index + 1, close_index


def parse_header_port_name(line_text: str) -> Optional[str]:
    """Extract the final port identifier from one module header line."""
    stripped = line_text.lstrip()
    if not stripped or stripped.startswith("//"):
        return None

    clean_line = strip_comments(line_text).strip()
    if not clean_line:
        return None

    clean_line = clean_line.rstrip(",").strip()
    if not clean_line:
        return None

    name_match = re.search(r"([A-Za-z_][A-Za-z0-9_$]*)\s*$", clean_line)
    if not name_match:
        return None
    name = name_match.group(1)
    if not IDENT_RE.match(name):
        return None
    return name


def parse_header_port_lines(source_text: str, content_start: int, content_end: int) -> List[HeaderPortLine]:
    """Parse recognizable one-line port declarations from a module header port-list span."""
    port_lines: List[HeaderPortLine] = []
    for line_start, line_end, line_text in iter_line_spans(source_text, content_start, content_end):
        port_name = parse_header_port_name(line_text)
        if port_name:
            port_lines.append(HeaderPortLine(name=port_name, start=line_start, end=line_end, text=line_text))
    return port_lines


def format_port_decl(direction: str, width: str, signal_name: str) -> str:
    """Format a Verilog port declaration line."""
    if width:
        return f"{direction} {width} {signal_name}"
    return f"{direction} {signal_name}"
