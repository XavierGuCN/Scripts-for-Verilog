# AutoPort

`AutoPort.py` 用来扫描一个 top module 中的例化模块，根据各子模块端口方向和同名连线关系，自动推导 top module 需要暴露的端口，并直接回写到原始 top module 定义中。

## 使用方式

```bash
python3 AutoPort.py path/to/top.v --top-module top_name --search-root path/to/project --show-refs
```

常用参数：

- `top_file`：包含 top module 的 Verilog 文件。
- `--top-module`：如果 `top_file` 内有多个 module，需要显式指定 top module 名称。
- `--search-root`：扫描子模块定义的工程根目录，默认当前目录。
- `--show-refs`：额外打印每个结论来自哪些例化端口，便于排查。

执行后脚本会直接覆盖 `top_file` 中的目标 top module，把推导出的端口定义写到 `module ... (...)` 上；终端中只保留端口汇总和内部连线提示。

## 规则实现

- 某个信号只连接到一个例化端口：
  - 子模块端口是 `input`，则 top 定义为 `input`
  - 子模块端口是 `output`，则 top 定义为 `output`
- 某个信号连接到多个例化端口，且方向全为 `input`：
  - 视为公共输入，top 定义为 `input`
- 某个信号连接到多个例化端口，且方向全为 `output`：
  - 视为错误，脚本直接退出
- 某个信号连接到多个例化端口，且同时存在 `input` 与 `output`：
  - 视为内部互联，不出现在 top 端口中，同时记入 `Potential Internal Outputs`

## 当前约束

- 目前仅支持命名端口连接：`.port(signal)`
- 连接表达式只支持简单信号名或常量，不支持拼接、位选、部分选通等复杂表达式
- 主要面向常见 ANSI/non-ANSI Verilog 端口声明；特别复杂的宏展开语法暂未覆盖
