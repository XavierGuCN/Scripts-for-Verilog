# Utils Reference

`PyUtils.py`、`VerilogTextUtils.py` 和 `VerilogLanguageUtils.py` 提供了当前脚本仓库里可复用的基础能力。

- `PyUtils.py`：通用文本/文件处理。
- `VerilogTextUtils.py`：Verilog 源码文本处理，例如注释、行 span、括号匹配、逗号、缩进、filelist。
- `VerilogLanguageUtils.py`：Verilog 语法解析和分析，例如端口、模块、实例、信号方向/位宽。

本文档包含：

- 函数说明
- 基本用法
- 使用示例
- 示例输出

## PyUtils.py

文件位置：[PyUtils.py](/Users/xaviergu/Library/CloudStorage/OneDrive-个人/0000_Working%20%26%20Study/Scripts%20for%20Verilog/utils/PyUtils.py)

### indent_block

作用：给多行文本中的每个非空行增加统一缩进。

函数签名：

```python
indent_block(text: str, indent: str = "    ") -> str
```

典型用途：

- 生成 Verilog `module` 头部参数列表
- 生成多行代码片段
- 输出格式化文本

示例：

```python
from PyUtils import indent_block

text = "input clk\n\noutput done"
result = indent_block(text, "  ")
print(result)
```

输出：

```text
  input clk

  output done
```

### ensure_trailing_newline

作用：保证文本末尾至少有一个换行符。

函数签名：

```python
ensure_trailing_newline(text: str) -> str
```

典型用途：

- 文件回写前统一结尾格式
- 避免生成的文本没有尾换行

示例：

```python
from PyUtils import ensure_trailing_newline

print(repr(ensure_trailing_newline("module top;")))
print(repr(ensure_trailing_newline("module top;\n")))
```

输出：

```text
'module top;\n'
'module top;\n'
```

### replace_span

作用：替换字符串中的一个半开区间 `[start, end)`。

函数签名：

```python
replace_span(text: str, span: tuple[int, int], replacement: str) -> str
```

典型用途：

- 用新的 `module` 文本替换原文件中的旧 `module`
- 按已知字符位置进行局部更新

示例：

```python
from PyUtils import replace_span

text = "abcdef"
result = replace_span(text, (2, 4), "XY")
print(result)
```

输出：

```text
abXYef
```

### collect_files_by_extensions

作用：递归扫描目录，返回所有匹配扩展名的文件路径。

函数签名：

```python
collect_files_by_extensions(search_root: Path, extensions: Sequence[str]) -> list[Path]
```

典型用途：

- 收集工程中的 `.v`、`.sv` 文件
- 为模块库加载提供输入文件列表

示例：

```python
from pathlib import Path
from PyUtils import collect_files_by_extensions

paths = collect_files_by_extensions(Path("examples/good"), [".v"])
for path in paths:
    print(path.name)
```

输出：

```text
mod_a.v
mod_b.v
mod_c.v
top.v
```

## VerilogTextUtils.py

文件位置：[VerilogTextUtils.py](/Users/xaviergu/Library/CloudStorage/OneDrive-个人/0000_Working%20%26%20Study/Scripts%20for%20Verilog/utils/VerilogTextUtils.py)

职责：处理 Verilog 源码文本和源码文件集合，不直接建模 Verilog module/port/signal 语义。

典型内容：

- `DEFAULT_VERILOG_EXTENSIONS`
- `collect_verilog_source_files`
- `strip_comments`
- `strip_comments_preserve_layout`
- `split_top_level_csv`
- `find_matching_paren`
- `iter_line_spans`
- `split_line_ending`
- `has_trailing_comma`
- `set_trailing_comma`
- `infer_port_indent`

## VerilogLanguageUtils.py

文件位置：[VerilogLanguageUtils.py](/Users/xaviergu/Library/CloudStorage/OneDrive-个人/0000_Working%20%26%20Study/Scripts%20for%20Verilog/utils/VerilogLanguageUtils.py)

职责：处理 Verilog 语法结构、信号关系和端口分析。

典型内容：

- Verilog regex：`MODULE_RE`、`INSTANCE_RE`、`PORT_CONNECTION_RE`、`DECL_RE`、`IDENT_RE`、`SIGNAL_REF_RE` 等
- 数据结构：`PortDef`、`ModuleDef`、`NamedConnection`、`InstanceDef`、`HeaderPortLine`、`InstancePortRef`、`SignalAnalysis`
- 语法解析：`parse_header_ports`、`parse_body_ports`、`parse_module_instances`、`parse_modules_from_file`
- 信号分析：`parse_instances`、`parse_internal_signal_names`、`ensure_same_width`、`analyze_signals`

### 常量和正则

#### DEFAULT_VERILOG_EXTENSIONS

作用：默认扫描的 Verilog/SystemVerilog 文件扩展名。

当前值：

```python
(".v", ".sv", ".vh", ".svh")
```

#### INTERNAL_SIGNAL_DECL_RE

作用：匹配 module body 内部的 `wire`、`reg`、`logic`、`tri` 等信号声明。

典型用途：

- 判断 top module 中某个连接信号是否已经被声明为内部信号
- 给 AutoPort/AutoWire 类脚本复用内部信号提取逻辑

### 数据结构

#### PortDef

作用：表示一个端口定义。

字段：

- `name`：端口名
- `direction`：方向，通常为 `input`、`output`、`inout`
- `width`：位宽文本，例如 `[7:0]`

补充属性：

- `width_clean`：去掉空格后的位宽文本

示例：

```python
from utils.VerilogLanguageUtils import PortDef

port = PortDef(name="data", direction="input", width="[ 7 : 0 ]")
print(port.width_clean)
```

输出：

```text
[7:0]
```

#### ModuleDef

作用：表示一个已解析的 module。

字段：

- `name`：module 名
- `file_path`：来源文件
- `ports`：端口字典
- `parameters`：参数列表原文
- `body`：module 主体原文
- `span`：该 module 在原文件中的字符区间

#### NamedConnection

作用：表示一个命名端口连接，例如 `.clk(sys_clk)`。

字段：

- `port_name`：端口名，例如 `clk`
- `expr`：连接表达式，例如 `sys_clk` 或 `cfg[2:0]`

#### InstanceDef

作用：表示一个已解析的模块实例。

字段：

- `module_name`：被实例化模块名
- `instance_name`：实例名
- `parameter_overrides`：参数覆盖文本
- `connections`：命名端口连接列表

#### HeaderPortLine

作用：表示 module header 端口列表中的一行端口文本及其源码位置。

字段：

- `name`：端口名
- `start`：该行在原始源码中的起始字符位置
- `end`：该行在原始源码中的结束字符位置
- `text`：该行原始文本

#### InstancePortRef

作用：表示一个 instance port 已经被解析并绑定到某个上层 signal。

字段：

- `instance_name`：实例名
- `module_name`：实例所属模块名
- `port_name`：子模块端口名
- `signal_name`：连接到的基础信号名
- `direction`：子模块端口方向
- `width`：去空白后的端口位宽

#### SignalAnalysis

作用：表示某个 signal 经过方向和位宽分析后的结果。

字段：

- `signal_name`：信号名
- `top_direction`：推断出的 top 方向；如果为 `None`，表示应保留为内部信号
- `width`：信号位宽
- `reason`：推断原因
- `refs`：贡献该分析结果的 `InstancePortRef` 列表

### strip_comments

作用：直接移除 Verilog 中的单行和多行注释。

函数签名：

```python
strip_comments(text: str) -> str
```

示例：

```python
from utils.VerilogTextUtils import strip_comments

text = "wire a; // keep line\n/* drop */ wire b;"
print(strip_comments(text))
```

输出：

```text
wire a; 
 wire b;
```

### strip_comments_preserve_layout

作用：把注释内容替换成空格，但保留换行和整体字符布局。

这个函数非常适合配合正则解析使用，因为它不会破坏原始文本的位置关系。

函数签名：

```python
strip_comments_preserve_layout(text: str) -> str
```

示例：

```python
from utils.VerilogTextUtils import strip_comments_preserve_layout

text = "a/*xx*/b // y\nc"
print(strip_comments_preserve_layout(text))
```

输出：

```text
a      b     
c
```

### normalize_width

作用：把位宽表达式中的空白去掉。

函数签名：

```python
normalize_width(width: str) -> str
```

示例：

```python
from utils.VerilogLanguageUtils import normalize_width

print(normalize_width("[ 15 : 0 ]"))
```

输出：

```text
[15:0]
```

### split_top_level_csv

作用：按顶层逗号切分字符串，忽略括号、方括号、大括号内部的逗号。

函数签名：

```python
split_top_level_csv(text: str) -> list[str]
```

典型用途：

- 拆分 module 头部端口列表
- 拆分参数列表
- 拆分声明列表

示例：

```python
from utils.VerilogTextUtils import split_top_level_csv

text = "a, func(x, y), bus[3:0], {m, n}"
print(split_top_level_csv(text))
```

输出：

```text
['a', 'func(x, y)', 'bus[3:0]', '{m, n}']
```

### normalize_decl_names

作用：从声明文本中提取信号名，并去除默认赋值和附着在名字后的维度。

函数签名：

```python
normalize_decl_names(names_text: str) -> list[str]
```

示例：

```python
from utils.VerilogLanguageUtils import normalize_decl_names

text = "a, b = 1'b0, cfg[2:0]"
print(normalize_decl_names(text))
```

输出：

```text
['a', 'b', 'cfg']
```

### parse_header_ports

作用：解析 ANSI 风格 module 头部中的端口定义。

函数签名：

```python
parse_header_ports(header_text: str) -> dict[str, PortDef]
```

示例：

```python
from utils.VerilogLanguageUtils import parse_header_ports

ports = parse_header_ports("input clk, output [7:0] data_out")
for name, port in ports.items():
    print(name, port.direction, port.width_clean)
```

输出：

```text
clk input 
data_out output [7:0]
```

### parse_body_ports

作用：解析 non-ANSI 风格端口声明。

函数签名：

```python
parse_body_ports(body_text: str) -> dict[str, PortDef]
```

示例：

```python
from utils.VerilogLanguageUtils import parse_body_ports

body = """
input clk;
output [3:0] data_out, data_dbg;
"""
ports = parse_body_ports(body)
for name, port in ports.items():
    print(name, port.direction, port.width_clean)
```

输出：

```text
clk input 
data_out output [3:0]
data_dbg output [3:0]
```

### parse_named_port_connections

作用：解析实例化语句中的命名端口连接。

函数签名：

```python
parse_named_port_connections(connections_text: str) -> list[NamedConnection]
```

示例：

```python
from utils.VerilogLanguageUtils import parse_named_port_connections

connections = parse_named_port_connections(".clk(sys_clk), .cfg(cfg[2:0])")
for item in connections:
    print(item.port_name, item.expr)
```

输出：

```text
clk sys_clk
cfg cfg[2:0]
```

### parse_module_instances

作用：从一个 module body 中提取实例化模块，自动忽略被注释掉的实例和端口连接。

函数签名：

```python
parse_module_instances(module_body: str) -> list[InstanceDef]
```

示例：

```python
from utils.VerilogLanguageUtils import parse_module_instances

body = """
mod_a u_a (
  .clk(sys_clk),
  // .dbg(dbg_sig),
  .cfg(cfg[2:0])
);
"""
instances = parse_module_instances(body)
for inst in instances:
    print(inst.module_name, inst.instance_name, len(inst.connections))
```

输出：

```text
mod_a u_a 2
```

### parse_instances

作用：解析 top module 中的实例化连接，并结合 module library 把 `.port(signal)` 转换成带方向和位宽的 `InstancePortRef`。

函数签名：

```python
parse_instances(top_module: ModuleDef, module_library: dict[str, ModuleDef]) -> list[InstancePortRef]
```

典型用途：

- AutoPort 推断 top 端口
- AutoWire/AutoReg 分析实例之间的连接关系
- 检查 instance 端口名是否存在于被例化模块定义中

### ensure_same_width

作用：检查同一个信号连接到的所有端口位宽是否一致，并返回统一位宽。

函数签名：

```python
ensure_same_width(signal_name: str, refs: Sequence[InstancePortRef]) -> str
```

如果位宽不一致，会抛出 `VerilogAnalysisError`。

### parse_internal_signal_names

作用：解析 module body 中已经声明的内部信号名。

函数签名：

```python
parse_internal_signal_names(top_module: ModuleDef) -> set[str]
```

典型用途：

- AutoPort 判断信号是否应该继续保留为内部信号
- AutoWire 类脚本避免重复声明已有 wire/logic

### infer_default_top_direction

作用：根据同一信号连接到的 instance port 方向，推断默认 top 方向。

函数签名：

```python
infer_default_top_direction(
    signal_name: str,
    refs: Sequence[InstancePortRef],
) -> tuple[str | None, str]
```

基本规则：

- 单连接：沿用该 instance port 方向
- 全部为 `input`：推断为 top `input`
- 全部为 `output`：视为多驱动并报错
- 包含 `inout`：推断为 top `inout`
- 同时包含 `input` 和 `output`：视为内部连接，返回 `None`

### analyze_signals

作用：汇总 `InstancePortRef`，把信号分类成 top port 或 internal signal。

函数签名：

```python
analyze_signals(
    refs: Sequence[InstancePortRef],
    internal_signal_names: set[str],
    forced_output_specs: set[tuple[str, str]],
) -> tuple[list[SignalAnalysis], list[SignalAnalysis]]
```

典型用途：

- AutoPort 的核心分析步骤
- 其他脚本复用 top port/internal signal 分类逻辑
- 配合 `forced_output_specs` 把指定 `module.port` 的 output 强制提升为 top output

### parse_modules_from_file

作用：从单个 Verilog 文件中解析出所有 module。

函数签名：

```python
parse_modules_from_file(file_path: Path) -> dict[str, ModuleDef]
```

示例：

```python
from pathlib import Path
from utils.VerilogLanguageUtils import parse_modules_from_file

modules = parse_modules_from_file(Path("examples/good/top.v"))
print(sorted(modules))
```

输出：

```text
['top']
```

### load_module_library

作用：从一个或多个目录、以及可选的 filelist 中加载所有 module 定义，如果有重名 module 会直接报错。

函数签名：

```python
load_module_library(
    search_roots: Path | Sequence[Path] | None,
    extensions: Sequence[str],
    filelists: Path | Sequence[Path] | None = None,
) -> dict[str, ModuleDef]
```

示例：

```python
from pathlib import Path
from utils.VerilogLanguageUtils import load_module_library
from utils.VerilogTextUtils import DEFAULT_VERILOG_EXTENSIONS

library = load_module_library(
    [Path("examples/good")],
    DEFAULT_VERILOG_EXTENSIONS,
)
print(sorted(library))
```

输出：

```text
['mod_a', 'mod_b', 'mod_c', 'top']
```

### collect_verilog_source_files

作用：统一收集用于解析 module 的 Verilog 源文件。

支持来源：

- 一个或多个搜索目录
- 一个或多个 filelist
- 嵌套 `-f/-F` 子 filelist
- filelist 中的 `-v` 源文件
- filelist 中的 `-y` 库目录

函数签名：

```python
collect_verilog_source_files(
    search_roots: Path | Sequence[Path] | None,
    extensions: Sequence[str],
    filelists: Path | Sequence[Path] | None = None,
) -> list[Path]
```

示例：

```python
from pathlib import Path
from utils.VerilogTextUtils import DEFAULT_VERILOG_EXTENSIONS, collect_verilog_source_files

files = collect_verilog_source_files(
    [Path("examples/good")],
    DEFAULT_VERILOG_EXTENSIONS,
)
for path in files:
    print(path.name)
```

输出：

```text
mod_a.v
mod_b.v
mod_c.v
top.v
```

### get_target_top_module

作用：从目标文件中获取 top module。

规则：

- 如果文件中只有一个 module，直接返回它
- 如果文件中有多个 module，则需要显式传入 `top_module_name`

函数签名：

```python
get_target_top_module(top_file: Path, top_module_name: str | None) -> ModuleDef
```

示例：

```python
from pathlib import Path
from utils.VerilogLanguageUtils import get_target_top_module

top = get_target_top_module(Path("examples/good/top.v"), None)
print(top.name)
```

输出：

```text
top
```

### extract_base_signal_name

作用：把简单连接表达式转换成基础信号名。

规则：

- `sys_clk` -> `sys_clk`
- `cfg[2:0]` -> `cfg`
- `1'b0` -> `None`
- 更复杂的拼接或表达式会抛出 `VerilogAnalysisError`

函数签名：

```python
extract_base_signal_name(expr: str) -> str | None
```

示例：

```python
from utils.VerilogLanguageUtils import extract_base_signal_name

print(extract_base_signal_name("cfg[2:0]"))
print(extract_base_signal_name("1'b0"))
```

输出：

```text
cfg
None
```

### find_matching_paren

作用：从指定的左括号位置开始，找到与之匹配的右括号位置。

函数签名：

```python
find_matching_paren(text: str, open_index: int) -> int
```

典型用途：

- 定位 module 参数列表或端口列表边界
- 在简单源码扫描中处理嵌套括号

### locate_header_port_list

作用：定位 module header 中端口列表内容的字符区间。

函数签名：

```python
locate_header_port_list(source_text: str, module_def: ModuleDef) -> tuple[int, int]
```

返回值是半开区间 `[start, end)`，不包含外层 `(` 和 `)`。

### iter_line_spans

作用：把指定文本区间按行切分，并保留每行在原始字符串中的字符区间。

函数签名：

```python
iter_line_spans(text: str, start: int, end: int) -> list[tuple[int, int, str]]
```

### parse_header_port_name

作用：从 module header 的一行文本中提取端口名。

函数签名：

```python
parse_header_port_name(line_text: str) -> str | None
```

说明：该函数适合一行一个端口的常见 header 风格；复杂宏或一行多个端口可能无法准确表达。

### parse_header_port_lines

作用：解析 module header 端口列表区间内可识别的端口行。

函数签名：

```python
parse_header_port_lines(
    source_text: str,
    content_start: int,
    content_end: int,
) -> list[HeaderPortLine]
```

典型用途：

- 增量更新 module header
- 保留原始端口行文本、注释和字符位置

### split_line_ending

作用：把一行文本拆成正文和换行符。

函数签名：

```python
split_line_ending(line_text: str) -> tuple[str, str]
```

支持 `\n` 和 `\r\n`。

### has_trailing_comma

作用：判断一行代码部分是否以逗号结尾，行尾注释会被忽略。

函数签名：

```python
has_trailing_comma(line_text: str) -> bool
```

### set_trailing_comma

作用：给一行端口文本添加或移除末尾逗号，同时尽量保留行尾注释。

函数签名：

```python
set_trailing_comma(line_text: str, should_have_comma: bool) -> str
```

### infer_port_indent

作用：根据已有 header 端口行推断新端口应该使用的缩进。

函数签名：

```python
infer_port_indent(
    existing_ports: Sequence[HeaderPortLine],
    source_text: str,
    content_start: int,
) -> str
```

### format_port_decl

作用：格式化一个 Verilog 端口声明字符串。

函数签名：

```python
format_port_decl(direction: str, width: str, signal_name: str) -> str
```

示例：

```python
from utils.VerilogLanguageUtils import format_port_decl

print(format_port_decl("input", "[7:0]", "data"))
print(format_port_decl("output", "", "done"))
```

输出：

```text
input [7:0] data
output done
```

## 推荐使用方式

### 给 AutoPort 类脚本复用

```python
from pathlib import Path

from utils.VerilogLanguageUtils import (
    analyze_signals,
    get_target_top_module,
    load_module_library,
    parse_instances,
    parse_internal_signal_names,
)
from utils.VerilogTextUtils import DEFAULT_VERILOG_EXTENSIONS

top = get_target_top_module(Path("top.v"), "top_example")
library = load_module_library(Path("."), DEFAULT_VERILOG_EXTENSIONS)
refs = parse_instances(top, library)
top_ports, internal_signals = analyze_signals(
    refs,
    parse_internal_signal_names(top),
    forced_output_specs=set(),
)

print(top.name)
print(len(top_ports), len(internal_signals))
```

### 给 AutoInst 类脚本复用

推荐重点复用：

- `parse_modules_from_file`
- `get_target_top_module`
- `format_port_decl`
- `split_top_level_csv`
- `parse_named_port_connections`

### 给 AutoWire/Reg 类脚本复用

推荐重点复用：

- `strip_comments_preserve_layout`
- `parse_module_instances`
- `parse_instances`
- `parse_internal_signal_names`
- `analyze_signals`
- `extract_base_signal_name`
- `normalize_width`
- `locate_header_port_list`
- `parse_header_port_lines`
- `set_trailing_comma`
- `replace_span`

## 说明

- 当前工具集主要面向常见 Verilog/SystemVerilog 语法
- 对宏展开、极复杂参数表达式、位置端口连接等高级场景暂未完全覆盖
- 如果后续加入 `AutoWire/Reg` 和 `AutoInst`，建议优先按职责往 `VerilogTextUtils.py` 或 `VerilogLanguageUtils.py` 中继续沉淀能力，而不是把解析逻辑散落到各入口脚本里
