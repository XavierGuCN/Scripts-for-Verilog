#!/usr/bin/env python3
"""Generate top-module port definitions from instantiated Verilog modules."""

# TODO.1：识别已有的顶层端口并保留它们（如果它们与实例连接兼容）。这将允许增量迁移而不是一次性重构。
# TODO.2：如果当前信号已在top module内被wire/reg声明，则将其保留为内部信号而不是提升为端口。这将允许更灵活的重构，而不仅仅是将所有连接提升为端口。

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

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
    parse_module_instances,
)


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

    try:
        module_library = load_module_library(search_roots, args.extensions, filelists=filelists)
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
