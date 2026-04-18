#!/usr/bin/env python3
"""Generate top-module port definitions from instantiated Verilog modules."""

from __future__ import annotations

import argparse
import re
import sys
from collections import defaultdict
from dataclasses import dataclass
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
)

INTERNAL_SIGNAL_DECL_RE = re.compile(
    r"\b(?:wire|reg|logic|tri|wand|wor|uwire)\b\s*"
    r"(?:signed|unsigned|\s)*"
    r"(\[[^\]]+\])?\s*"
    r"([^;]+?)\s*;",
    re.S,
)
FORCE_OUTPUT_SPEC_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_$]*)\.([A-Za-z_][A-Za-z0-9_$]*)$")


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


def infer_preserved_port_direction(refs: Sequence[InstancePortRef]) -> str:
    directions = {ref.direction for ref in refs}
    if directions == {"inout"} or "inout" in directions:
        return "inout"
    if "output" in directions:
        return "output"
    return "input"


def order_top_ports(
    top_ports_by_name: Dict[str, SignalAnalysis],
    existing_ports: Dict[str, object],
    preserve_existing: bool,
) -> List[SignalAnalysis]:
    if not preserve_existing:
        return [top_ports_by_name[name] for name in sorted(top_ports_by_name)]

    ordered_ports: List[SignalAnalysis] = []
    remaining = dict(top_ports_by_name)
    for signal_name in existing_ports:
        if signal_name in remaining:
            ordered_ports.append(remaining.pop(signal_name))
    for signal_name in sorted(remaining):
        ordered_ports.append(remaining[signal_name])
    return ordered_ports


def analyze_signals(
    refs: Sequence[InstancePortRef],
    existing_ports: Dict[str, object],
    preserve_existing: bool,
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

        if preserve_existing and signal_name in existing_ports:
            top_ports_by_name[signal_name] = SignalAnalysis(
                signal_name=signal_name,
                top_direction=infer_preserved_port_direction(signal_refs),
                width=width,
                reason="preserved existing top port and normalized its direction/width",
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

    return order_top_ports(top_ports_by_name, existing_ports, preserve_existing), internal_signals


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
    internal_signals: Sequence[SignalAnalysis],
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
        choices=("replace", "preserve"),
        default="replace",
        help=(
            "Port regeneration strategy: 'replace' rebuilds the top port list from scratch; "
            "'preserve' keeps connected existing top ports and normalizes their direction/width."
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
            existing_ports=top_module.ports,
            preserve_existing=args.port_list_mode == "preserve",
            internal_signal_names=parse_internal_signal_names(top_module),
            forced_output_specs=forced_output_specs,
        )
    except VerilogAnalysisError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    write_top_module(top_module, top_ports)
    print_report(top_module, top_ports, internal_signals, args.show_refs)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
