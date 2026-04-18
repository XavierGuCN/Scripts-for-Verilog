# Good Examples

`examples/good` 里包含几组可直接运行的 demo，用来验证 `AutoPort.py` 的主要能力。

## 1. 全量重建 port list

```bash
python3 AutoPort.py examples/good/top.v \
  --top-module top_example \
  --search-root examples/good \
  --show-refs
```

预期现象：

- 重新生成完整的 top port list
- `mid` 保持为内部互联，不提升为 top port

## 2. preserve 模式增量迁移

```bash
python3 AutoPort.py examples/good/top_preserve.v \
  --top-module top_preserve_example \
  --search-root examples/good \
  --port-list-mode preserve \
  --show-refs
```

预期现象：

- 已有的 `cfg`、`mid` 被保留
- `cfg` 会被修正为 `input [2:0]`
- `mid` 会被修正为 `output [7:0]`
- 已无连接的 `stale_port` 会被删除
- 已在 top 内声明的 `data_out` 会保留为内部 `wire`

## 3. replace 模式下保留内部信号

```bash
python3 AutoPort.py examples/good/top_internal_signal.v \
  --top-module top_internal_signal_example \
  --search-root examples/good \
  --show-refs
```

预期现象：

- `data_out` 不会被提升为 top port
- `data_out` 会出现在 `Internal Signals`

## 4. force-output-list 强制外提 output

```bash
python3 AutoPort.py examples/good/top_force_output.v \
  --top-module top_force_output_example \
  --search-root examples/good \
  --force-output-list examples/good/force_outputs.list \
  --show-refs
```

预期现象：

- `force_outputs.list` 中配置的 `mod_a.mid` 会把 `mid` 强制生成成 top `output [7:0]`
- 即使 `mid` 同时连接了 `mod_a.output` 和 `mod_b.input`，也不会再仅仅作为内部互联保留
