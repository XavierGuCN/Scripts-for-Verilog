# AutoPort

`AutoPort.py` 用来扫描一个 top module 中的例化模块，根据各子模块端口方向和同名连线关系，自动推导 top module 需要暴露的端口，并直接回写到原始 top module 定义中。

脚本默认使用增量更新模式，保留已有 header 文本，只追加缺失端口并注释掉明确无效的旧端口。也可以切换到 `replace` 模式，删除当前 top 的 port list 并按实例连接结果重新生成。

## 使用方式

```bash
python3 AutoPort.py path/to/top.v \
  --top-module top_name \
  --search-root path/to/project_a \
  --search-root path/to/project_b \
  --filelist path/to/files.f \
  --show-refs
```

常用参数：

- `top_file`：包含 top module 的 Verilog 文件。
- `--top-module`：如果 `top_file` 内有多个 module，需要显式指定 top module 名称。
- `--search-root`：扫描子模块定义的工程根目录。可重复指定多个目录；如果没有传 `--filelist`，默认扫描当前目录。
- `--filelist`：按 filelist 收集 Verilog 源文件。支持嵌套 `-f/-F` 子 filelist，也支持常见的 `-v` 源文件和 `-y` 库目录。
- `--mode {incr,replace}`：选择 top port list 更新策略。默认 `incr`。
- `--force-output-list`：指定一份文本列表文件，强制某些 `module_name.port_name` 对应的 `output` 端口继续向当前 top 伸出。可重复指定多份列表。
- `--show-refs`：额外打印每个结论来自哪些例化端口，便于排查。

示例：

```bash
python3 AutoPort.py rtl/top.v \
  --top-module top_example \
  --search-root ./ip/common \
  --search-root ./ip/vendor
```

```bash
python3 AutoPort.py rtl/top.v \
  --top-module top_example \
  --filelist ./build/all_sources.f
```

```bash
python3 AutoPort.py rtl/top.v \
  --top-module top_example \
  --search-root ./rtl
```

```bash
python3 AutoPort.py rtl/top.v \
  --top-module top_example \
  --search-root ./rtl \
  --mode replace
```

```bash
python3 AutoPort.py rtl/top.v \
  --top-module top_example \
  --filelist ./build/all_sources.f \
  --force-output-list ./cfg/force_outputs.list
```

`incr` 模式只更新目标 top module 的 header port list；`replace` 模式会直接覆盖 `top_file` 中的目标 top module，把推导出的端口定义写到 `module ... (...)` 上。终端中会输出 `Port Summary` 与 `Internal Signals` 两段汇总。

## 规则实现

- `replace` 模式完全忽略当前 top 里已有的端口定义，按实例连接结果重新生成。
- `incr` 模式保留已有端口的原始顺序、注释和格式。
- `incr` 模式只把缺失的新端口追加到已有端口之后，并包在 `/* autoport new YYYYMMDD begin */` 和 `/* autoport new YYYYMMDD end */` 之间。
- `incr` 模式会把没有任何实例连接的旧端口视为明确无效端口，整行注释掉，并在行末追加 `// delete YYYYMMDD`。
- `incr` 模式不会修正已有端口的方向或位宽；这类不一致留给 lint 检查。
- 如果某个信号已经在 top module 内被声明为 `wire/reg/logic/tri/wand/wor/uwire`：
  - 默认保留为内部信号，不自动提升为 top port。
- 如果某个信号命中了 `--force-output-list` 中配置的 `module_name.port_name`：
  - 只要该端口在模块定义中确认为 `output`，就会被强制生成为当前 top 的 `output`。
  - 这条规则优先级高于“保留为内部信号”的自动判断。
- 某个信号只连接到一个例化端口：
  - 子模块端口是 `input`，则 top 定义为 `input`
  - 子模块端口是 `output`，则 top 定义为 `output`
- 某个信号连接到多个例化端口，且方向全为 `input`：
  - 视为公共输入，top 定义为 `input`
- 某个信号连接到多个例化端口，且方向全为 `output`：
  - 视为错误，脚本直接退出
- 某个信号连接到多个例化端口，且同时存在 `input` 与 `output`：
  - 默认视为内部互联，不出现在 top 端口中，同时记入 `Internal Signals`
  - 如果该信号命中 `--force-output-list`，则强制生成为 `output`
- 某个信号包含 `inout` 连接：
  - top 定义为 `inout`

## force-output 列表格式

每行一个条目，格式固定为：

```text
module_def_name.port_name
```

例如：

```text
mod_a.mid
mod_debug.status
```

说明：

- 只支持模块定义名和端口名，不写实例名。
- 只允许引用真正的 `output` 端口；如果写到 `input/inout` 或不存在的端口，脚本会报错。
- 支持空行。
- 支持 `#` 和 `//` 注释。

## 当前约束

- 目前仅支持命名端口连接：`.port(signal)`
- 连接表达式只支持简单信号名或常量，不支持拼接、位选、部分选通等复杂表达式
- 主要面向常见 ANSI/non-ANSI Verilog 端口声明；特别复杂的宏展开语法暂未覆盖
- filelist 当前主要覆盖常见工程写法：裸源文件、`-f/-F`、`-v`、`-y`，以及常见的 `+incdir+`/编译选项跳过
