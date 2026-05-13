#!/usr/bin/env python3
"""Generate top-module port definitions from instantiated Verilog modules."""

from __future__ import annotations

import argparse
import re
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Set, Tuple

# Allow direct execution via `python3 AutoPort/AutoPort.py ...` by making the
# repository root importable before loading shared helpers from `utils`.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from utils.PyUtils import ensure_trailing_newline, indent_block, replace_span
from utils.VerilogUtils import (
    DEFAULT_VERILOG_EXTENSIONS,
    ModuleDef,
    VerilogAnalysisError,
    extract_base_signal_name,
    format_port_decl,
    get_target_top_module,
    load_module_library,
    normalize_decl_names,
    parse_module_instances,
    strip_comments,
    strip_comments_preserve_layout,
)

INTERNAL_SIGNAL_DECL_RE = re.compile(
    r"\b(?:wire|reg|logic|tri|wand|wor|uwire)\b\s*"
    r"(?:signed|unsigned|\s)*"
    r"(\[[^\]]+\])?\s*"
    r"([^;]+?)\s*;",
    re.S,
)
FORCE_OUTPUT_SPEC_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_$]*)\.([A-Za-z_][A-Za-z0-9_$]*)$")
IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")


@dataclass
class HeaderPortLine:
    name: str
    start: int
    end: int
    text: str


@dataclass
class IncrementalUpdateSummary:
    preserved_ports: List[str]
    added_ports: List[str]
    deleted_ports: List[str]


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


def parse_instances(top_module: ModuleDef, module_library: Dict[str, ModuleDef]) -> List[InstancePortRef]:
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
    widths = {ref.width for ref in refs}
    if len(widths) > 1:
        detail = ", ".join(
            f"{ref.instance_name}.{ref.port_name}={ref.width or 'scalar'}" for ref in refs
        )
        raise VerilogAnalysisError(f"Width mismatch detected on signal '{signal_name}': {detail}")
    return next(iter(widths), "")


def parse_internal_signal_names(top_module: ModuleDef) -> Set[str]:
    declared_signals: Set[str] = set()
    body_clean = strip_comments(top_module.body)
    for _width, names_text in INTERNAL_SIGNAL_DECL_RE.findall(body_clean):
        declared_signals.update(normalize_decl_names(names_text))
    return declared_signals


def load_force_output_specs(
    spec_files: Sequence[Path],
    module_library: Dict[str, ModuleDef],
) -> Set[Tuple[str, str]]:
    forced_specs: Set[Tuple[str, str]] = set()
    for spec_file in spec_files:
        for line_no, raw_line in enumerate(spec_file.read_text(encoding="utf-8").splitlines(), start=1):
            stripped = raw_line.split("//", 1)[0].split("#", 1)[0].strip()
            if not stripped:
                continue

            match = FORCE_OUTPUT_SPEC_RE.fullmatch(stripped)
            if not match:
                raise VerilogAnalysisError(
                    f"Invalid force-output entry '{stripped}' in '{spec_file}' line {line_no}. "
                    "Expected format: module_name.port_name"
                )

            module_name, port_name = match.groups()
            if module_name not in module_library:
                raise VerilogAnalysisError(
                    f"Module '{module_name}' referenced by '{spec_file}' line {line_no} was not found."
                )
            port_def = module_library[module_name].ports.get(port_name)
            if port_def is None:
                raise VerilogAnalysisError(
                    f"Port '{port_name}' on module '{module_name}' referenced by '{spec_file}' line {line_no} was not found."
                )
            if port_def.direction != "output":
                raise VerilogAnalysisError(
                    f"Only output ports can be forced. '{module_name}.{port_name}' is '{port_def.direction}'."
                )
            forced_specs.add((module_name, port_name))
    return forced_specs


def infer_default_top_direction(signal_name: str, refs: Sequence[InstancePortRef]) -> Tuple[Optional[str], str]:
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


def skip_whitespace(text: str, index: int) -> int:
    while index < len(text) and text[index].isspace():
        index += 1
    return index


def find_matching_paren(text: str, open_index: int) -> int:
    depth = 0
    for index in range(open_index, len(text)):
        char = text[index]
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return index
    raise VerilogAnalysisError("Could not find matching ')' in module header.")


def locate_header_port_list(source_text: str, module_def: ModuleDef) -> Tuple[int, int]:
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
        index = find_matching_paren(clean_text, index) + 1
        index = skip_whitespace(clean_text, index)

    if index >= module_end or clean_text[index] != "(":
        raise VerilogAnalysisError(f"Could not locate port list in module '{module_def.name}'.")

    close_index = find_matching_paren(clean_text, index)
    return index + 1, close_index


def iter_line_spans(text: str, start: int, end: int) -> List[Tuple[int, int, str]]:
    lines: List[Tuple[int, int, str]] = []
    index = start
    while index < end:
        newline_index = text.find("\n", index, end)
        line_end = end if newline_index == -1 else newline_index + 1
        lines.append((index, line_end, text[index:line_end]))
        index = line_end
    return lines


def parse_header_port_name(line_text: str) -> Optional[str]:
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
    port_lines: List[HeaderPortLine] = []
    for line_start, line_end, line_text in iter_line_spans(source_text, content_start, content_end):
        port_name = parse_header_port_name(line_text)
        if port_name:
            port_lines.append(HeaderPortLine(name=port_name, start=line_start, end=line_end, text=line_text))
    return port_lines


def split_line_ending(line_text: str) -> Tuple[str, str]:
    if line_text.endswith("\r\n"):
        return line_text[:-2], "\r\n"
    if line_text.endswith("\n"):
        return line_text[:-1], "\n"
    return line_text, ""


def comment_deleted_port_line(line_text: str, timestamp: str) -> str:
    body, newline = split_line_ending(line_text)
    indent_match = re.match(r"^(\s*)", body)
    indent = indent_match.group(1) if indent_match else ""
    content = body[len(indent):].rstrip()
    if not content:
        return line_text
    return f"{indent}// {content} // delete {timestamp}{newline}"


def has_trailing_comma(line_text: str) -> bool:
    clean_line = strip_comments(line_text).rstrip()
    return clean_line.endswith(",")


def set_trailing_comma(line_text: str, should_have_comma: bool) -> str:
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


def infer_port_indent(existing_ports: Sequence[HeaderPortLine], source_text: str, content_start: int) -> str:
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


def build_autoport_block(new_ports: Sequence[SignalAnalysis], indent: str, timestamp: str) -> str:
    lines = [f"{indent}/* autoport new {timestamp} begin */"]
    for index, port in enumerate(new_ports):
        suffix = "," if index < len(new_ports) - 1 else ""
        lines.append(f"{indent}{format_port_decl(port.top_direction or 'wire', port.width, port.signal_name)}{suffix}")
    lines.append(f"{indent}/* autoport new {timestamp} end */")
    return "\n".join(lines) + "\n"


def patch_top_module_incremental(
    top_module: ModuleDef,
    top_ports: Sequence[SignalAnalysis],
    refs: Sequence[InstancePortRef],
) -> IncrementalUpdateSummary:
    source_text = top_module.file_path.read_text(encoding="utf-8")
    content_start, content_end = locate_header_port_list(source_text, top_module)
    existing_ports = parse_header_port_lines(source_text, content_start, content_end)
    existing_names = {port.name for port in existing_ports}
    connected_names = {ref.signal_name for ref in refs}
    ports_to_add = [port for port in top_ports if port.signal_name not in existing_names]
    ports_to_delete = [port for port in existing_ports if port.name not in connected_names]
    preserved_names = [
        port.name
        for port in existing_ports
        if port.name in connected_names and port not in ports_to_delete
    ]
    deleted_names = [port.name for port in ports_to_delete]
    timestamp = datetime.now().strftime("%Y%m%d")

    replacements: Dict[Tuple[int, int], str] = {}
    deleted_spans = {(port.start, port.end) for port in ports_to_delete}
    for port in ports_to_delete:
        replacements[(port.start, port.end)] = comment_deleted_port_line(port.text, timestamp)

    active_ports = [port for port in existing_ports if (port.start, port.end) not in deleted_spans]
    if active_ports:
        last_active = active_ports[-1]
        current_text = replacements.get((last_active.start, last_active.end), last_active.text)
        replacements[(last_active.start, last_active.end)] = set_trailing_comma(
            current_text,
            should_have_comma=bool(ports_to_add),
        )

    updated_parts: List[str] = []
    cursor = content_start
    for (start, end), replacement in sorted(replacements.items()):
        updated_parts.append(source_text[cursor:start])
        updated_parts.append(replacement)
        cursor = end
    updated_parts.append(source_text[cursor:content_end])
    updated_content = "".join(updated_parts)

    if ports_to_add:
        indent = infer_port_indent(existing_ports, source_text, content_start)
        if not updated_content.endswith("\n"):
            updated_content += "\n"
        updated_content += build_autoport_block(ports_to_add, indent, timestamp)
    elif not active_ports:
        updated_content = re.sub(r",\s*$", "", updated_content.rstrip()) + ("\n" if updated_content.strip() else "")

    updated_text = source_text[:content_start] + updated_content + source_text[content_end:]
    updated_text = ensure_trailing_newline(updated_text)
    top_module.file_path.write_text(updated_text, encoding="utf-8")

    return IncrementalUpdateSummary(
        preserved_ports=preserved_names,
        added_ports=[port.signal_name for port in ports_to_add],
        deleted_ports=deleted_names,
    )


def print_report(
    top_module: ModuleDef,
    top_ports: Sequence[SignalAnalysis],
    internal_signals: Sequence[SignalAnalysis],
    show_refs: bool,
    incremental_summary: Optional[IncrementalUpdateSummary] = None,
) -> None:
    print(f"Updated top module '{top_module.name}' in '{top_module.file_path}'")
    if incremental_summary is not None:
        print()
        print("=== Incremental Update Summary ===")
        preserved = (
            ", ".join(incremental_summary.preserved_ports)
            if incremental_summary.preserved_ports
            else "(none)"
        )
        added = ", ".join(incremental_summary.added_ports) if incremental_summary.added_ports else "(none)"
        deleted = ", ".join(incremental_summary.deleted_ports) if incremental_summary.deleted_ports else "(none)"
        print(f"Preserved existing ports: {preserved}")
        print(f"Added ports: {added}")
        print(f"Commented deleted ports: {deleted}")
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
    print("=== Internal Signals ===")
    if not internal_signals:
        print("(none)")
    for signal in internal_signals:
        print(f"{signal.signal_name}    // {signal.reason}")
        if show_refs:
            for ref in signal.refs:
                width_text = ref.width or "scalar"
                print(
                    f"    - {ref.instance_name} ({ref.module_name}).{ref.port_name} [{ref.direction}, {width_text}]"
                )


def write_top_module(top_module: ModuleDef, top_ports: Sequence[SignalAnalysis]) -> None:
    source_text = top_module.file_path.read_text(encoding="utf-8")
    updated_module = format_top_module(top_module, top_ports)
    updated_text = replace_span(source_text, top_module.span, updated_module)
    updated_text = ensure_trailing_newline(updated_text)
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
        action="append",
        default=[],
        help="Project root used to search for instantiated module definitions. Can be specified multiple times.",
    )
    parser.add_argument(
        "--filelist",
        action="append",
        default=[],
        help="Verilog filelist to load module sources from. Supports nested -f/-F filelists.",
    )
    parser.add_argument(
        "--extensions",
        nargs="+",
        default=list(DEFAULT_VERILOG_EXTENSIONS),
        help="File extensions to scan for module definitions.",
    )
    parser.add_argument(
        "--port-list-mode",
        choices=("replace", "incremental"),
        default="replace",
        help=(
            "Port update strategy: 'replace' rebuilds the whole top port list; "
            "'incremental' preserves existing header text, appends missing ports, and comments deleted ports."
        ),
    )
    parser.add_argument(
        "--force-output-list",
        action="append",
        default=[],
        help=(
            "Text file containing module_name.port_name entries. Matching output ports are forced "
            "to become top-module outputs. Can be specified multiple times."
        ),
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
    filelists = [Path(filelist).resolve() for filelist in args.filelist]
    force_output_lists = [Path(path).resolve() for path in args.force_output_list]
    search_roots = [Path(search_root).resolve() for search_root in args.search_root]
    if not search_roots and not filelists:
        search_roots = [Path(".").resolve()]

    if not top_file.is_file():
        parser.error(f"Top file does not exist: {top_file}")
    for search_root in search_roots:
        if not search_root.is_dir():
            parser.error(f"Search root is not a directory: {search_root}")
    for filelist in filelists:
        if not filelist.is_file():
            parser.error(f"Filelist does not exist: {filelist}")
    for spec_file in force_output_lists:
        if not spec_file.is_file():
            parser.error(f"Force-output list does not exist: {spec_file}")

    try:
        module_library = load_module_library(search_roots, args.extensions, filelists=filelists)
        top_module = get_target_top_module(top_file, args.top_module)
        refs = parse_instances(top_module, module_library)
        forced_output_specs = load_force_output_specs(force_output_lists, module_library)
        top_ports, internal_signals = analyze_signals(
            refs=refs,
            internal_signal_names=parse_internal_signal_names(top_module),
            forced_output_specs=forced_output_specs,
        )
    except VerilogAnalysisError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    incremental_summary: Optional[IncrementalUpdateSummary] = None
    if args.port_list_mode == "incremental":
        incremental_summary = patch_top_module_incremental(top_module, top_ports, refs)
    else:
        write_top_module(top_module, top_ports)
    print_report(top_module, top_ports, internal_signals, args.show_refs, incremental_summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
