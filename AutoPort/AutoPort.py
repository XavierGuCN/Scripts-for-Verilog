#!/usr/bin/env python3
"""Generate top-module port definitions from instantiated Verilog modules."""

from __future__ import annotations

import argparse
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple


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

PARAM_SPLIT_RE = re.compile(r",(?![^\(\[]*[\]\)])")
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
    name: str
    direction: str
    width: str = ""

    @property
    def width_clean(self) -> str:
        return normalize_width(self.width)


@dataclass
class ModuleDef:
    name: str
    file_path: Path
    ports: Dict[str, PortDef] = field(default_factory=dict)
    parameters: str = ""
    body: str = ""
    span: Tuple[int, int] = (0, 0)


@dataclass
class InstancePortRef:
    instance_name: str
    module_name: str
    port_name: str
    signal_name: str
    direction: str
    width: str


@dataclass
class SignalAnalysis:
    signal_name: str
    top_direction: Optional[str]
    width: str
    reason: str
    refs: List[InstancePortRef]


def strip_comments(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    text = re.sub(r"//.*?$", "", text, flags=re.M)
    return text


def strip_comments_preserve_layout(text: str) -> str:
    def replace_block(match: re.Match[str]) -> str:
        return re.sub(r"[^\n]", " ", match.group(0))

    def replace_line(match: re.Match[str]) -> str:
        return re.sub(r"[^\n]", " ", match.group(0))

    text = re.sub(r"/\*.*?\*/", replace_block, text, flags=re.S)
    text = re.sub(r"//.*?$", replace_line, text, flags=re.M)
    return text


def normalize_width(width: str) -> str:
    return re.sub(r"\s+", "", width or "")


def split_top_level_csv(text: str) -> List[str]:
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


def normalize_decl_names(names_text: str) -> List[str]:
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
    ports: Dict[str, PortDef] = {}
    for direction, width, names_text in DECL_RE.findall(body_text):
        for name in normalize_decl_names(names_text):
            ports[name] = PortDef(name=name, direction=direction, width=width or "")
    return ports


def parse_modules_from_file(file_path: Path) -> Dict[str, ModuleDef]:
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


def load_module_library(search_root: Path, extensions: Sequence[str]) -> Dict[str, ModuleDef]:
    module_defs: Dict[str, ModuleDef] = {}
    patterns = [f"**/*{ext}" for ext in extensions]
    file_paths = set()
    for pattern in patterns:
        file_paths.update(search_root.glob(pattern))

    for file_path in sorted(file_paths):
        if not file_path.is_file():
            continue
        for module_name, module_def in parse_modules_from_file(file_path).items():
            if module_name in module_defs:
                raise VerilogAnalysisError(
                    f"Module '{module_name}' is defined in both '{module_defs[module_name].file_path}' and '{file_path}'"
                )
            module_defs[module_name] = module_def
    return module_defs


def get_target_top_module(top_file: Path, top_module_name: Optional[str]) -> ModuleDef:
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


def extract_signal_name(expr: str) -> Optional[str]:
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
    refs: List[InstancePortRef] = []
    body_without_comments = strip_comments_preserve_layout(top_module.body)
    for match in INSTANCE_RE.finditer(body_without_comments):
        submodule_name = match.group(1)
        instance_name = match.group(3)
        connections_text = match.group(4)

        if submodule_name not in module_library:
            raise VerilogAnalysisError(
                f"Definition for instantiated module '{submodule_name}' (instance '{instance_name}') was not found."
            )
        submodule_def = module_library[submodule_name]

        for port_name, expr in PORT_CONNECTION_RE.findall(connections_text):
            if port_name not in submodule_def.ports:
                raise VerilogAnalysisError(
                    f"Port '{port_name}' on instance '{instance_name}' was not found in module '{submodule_name}'."
                )
            signal_name = extract_signal_name(expr)
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
    widths = {ref.width for ref in refs}
    if len(widths) > 1:
        detail = ", ".join(
            f"{ref.instance_name}.{ref.port_name}={ref.width or 'scalar'}" for ref in refs
        )
        raise VerilogAnalysisError(f"Width mismatch detected on signal '{signal_name}': {detail}")
    return next(iter(widths), "")


def analyze_signals(refs: Sequence[InstancePortRef]) -> Tuple[List[SignalAnalysis], List[SignalAnalysis]]:
    by_signal: Dict[str, List[InstancePortRef]] = defaultdict(list)
    for ref in refs:
        by_signal[ref.signal_name].append(ref)

    top_ports: List[SignalAnalysis] = []
    potential_outputs: List[SignalAnalysis] = []

    for signal_name in sorted(by_signal):
        signal_refs = by_signal[signal_name]
        width = ensure_same_width(signal_name, signal_refs)
        directions = {ref.direction for ref in signal_refs}

        if len(signal_refs) == 1:
            only_ref = signal_refs[0]
            top_ports.append(
                SignalAnalysis(
                    signal_name=signal_name,
                    top_direction=only_ref.direction,
                    width=width,
                    reason="single connection",
                    refs=signal_refs,
                )
            )
            continue

        if directions == {"input"}:
            top_ports.append(
                SignalAnalysis(
                    signal_name=signal_name,
                    top_direction="input",
                    width=width,
                    reason="common input",
                    refs=signal_refs,
                )
            )
            continue

        if directions == {"output"}:
            detail = ", ".join(f"{ref.instance_name}.{ref.port_name}" for ref in signal_refs)
            raise VerilogAnalysisError(
                f"Signal '{signal_name}' is driven by multiple output ports only: {detail}"
            )

        if directions.issubset({"input", "output"}):
            potential_outputs.append(
                SignalAnalysis(
                    signal_name=signal_name,
                    top_direction=None,
                    width=width,
                    reason="internal connection with both input and output endpoints",
                    refs=signal_refs,
                )
            )
            continue

        if directions == {"inout"} or "inout" in directions:
            top_ports.append(
                SignalAnalysis(
                    signal_name=signal_name,
                    top_direction="inout",
                    width=width,
                    reason="contains inout connection",
                    refs=signal_refs,
                )
            )
            continue

        raise VerilogAnalysisError(
            f"Unsupported direction combination on signal '{signal_name}': {', '.join(sorted(directions))}"
        )

    return top_ports, potential_outputs


def format_port_decl(direction: str, width: str, signal_name: str) -> str:
    if width:
        return f"{direction} {width} {signal_name}"
    return f"{direction} {signal_name}"


def indent_block(text: str, indent: str = "    ") -> str:
    return "\n".join(f"{indent}{line}" if line.strip() else "" for line in text.splitlines())


def format_top_module(module_def: ModuleDef, top_ports: Sequence[SignalAnalysis]) -> str:
    port_lines = [format_port_decl(port.top_direction or "wire", port.width, port.signal_name) for port in top_ports]
    if module_def.parameters:
        header_lines = [
            f"module {module_def.name} #(",
            indent_block(module_def.parameters),
            ")",
        ]
    else:
        header_lines = [f"module {module_def.name}"]

    if not port_lines:
        header_lines[-1] = f"{header_lines[-1]} ();"
    else:
        joined = ",\n    ".join(port_lines)
        header_lines[-1] = f"{header_lines[-1]} ("
        header_lines.append(f"    {joined}")
        header_lines.append(");")

    body = module_def.body.strip("\n")
    if body.strip():
        return "\n".join(header_lines) + "\n\n" + body + "\n\nendmodule"
    return "\n".join(header_lines) + "\n\nendmodule"


def print_report(
    top_module: ModuleDef,
    top_ports: Sequence[SignalAnalysis],
    potential_outputs: Sequence[SignalAnalysis],
    show_refs: bool,
) -> None:
    print(f"Updated top module '{top_module.name}' in '{top_module.file_path}'")
    print()
    print("=== Port Summary ===")
    if not top_ports:
        print("(none)")
    for port in top_ports:
        print(f"{format_port_decl(port.top_direction or 'wire', port.width, port.signal_name)}    // {port.reason}")
        if show_refs:
            for ref in port.refs:
                width_text = ref.width or "scalar"
                print(
                    f"    - {ref.instance_name} ({ref.module_name}).{ref.port_name} [{ref.direction}, {width_text}]"
                )

    print()
    print("=== Potential Internal Outputs ===")
    if not potential_outputs:
        print("(none)")
    for signal in potential_outputs:
        print(f"{signal.signal_name}    // {signal.reason}")
        if show_refs:
            for ref in signal.refs:
                width_text = ref.width or "scalar"
                print(
                    f"    - {ref.instance_name} ({ref.module_name}).{ref.port_name} [{ref.direction}, {width_text}]"
                )


def write_top_module(top_module: ModuleDef, top_ports: Sequence[SignalAnalysis]) -> None:
    source_text = top_module.file_path.read_text(encoding="utf-8")
    start, end = top_module.span
    updated_module = format_top_module(top_module, top_ports)
    updated_text = source_text[:start] + updated_module + source_text[end:]
    if not updated_text.endswith("\n"):
        updated_text += "\n"
    top_module.file_path.write_text(updated_text, encoding="utf-8")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Analyze instantiated Verilog modules and generate top-module port definitions."
    )
    parser.add_argument("top_file", help="Verilog file containing the top module with submodule instantiations.")
    parser.add_argument(
        "--top-module",
        help="Top module name. Required when the input file contains multiple module definitions.",
    )
    parser.add_argument(
        "--search-root",
        default=".",
        help="Project root used to search for instantiated module definitions. Default: current directory.",
    )
    parser.add_argument(
        "--extensions",
        nargs="+",
        default=[".v", ".sv", ".vh", ".svh"],
        help="File extensions to scan for module definitions.",
    )
    parser.add_argument(
        "--show-refs",
        action="store_true",
        help="Print which instance ports contributed to each result.",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    top_file = Path(args.top_file).resolve()
    search_root = Path(args.search_root).resolve()

    if not top_file.is_file():
        parser.error(f"Top file does not exist: {top_file}")
    if not search_root.is_dir():
        parser.error(f"Search root is not a directory: {search_root}")

    try:
        module_library = load_module_library(search_root, args.extensions)
        top_module = get_target_top_module(top_file, args.top_module)
        refs = parse_instances(top_module, module_library)
        top_ports, potential_outputs = analyze_signals(refs)
    except VerilogAnalysisError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    write_top_module(top_module, top_ports)
    print_report(top_module, top_ports, potential_outputs, args.show_refs)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
