# ADR-0009 SKiDL 自动画原理图：30 个以上元件的实测（M0 待验证项③）

- 状态：实测完成；可读性等用户看图判断，默认选项等用户定（2026-10-31）
- 影响：PLAN §2「原理图生成」、§15 第 1 条风险、§16

## 实测（SKiDL 3.0.0 + schematizer 0.2.0，KiCad 10.0.8）

素材：KiCad 示例 pic_programmer（63 个元件、2 页）。示例原理图 → kicad-cli 网表 → design.json → `eda_agent/emit/skidl_emit.py`。
运行目录 `runs/m0-pic-20261031-161138/`。

| 输入 | 选项 | 结果 |
|---|---|---|
| 全部 63 个元件，单页（含 6 个完全不连线的安装孔） | 默认 | 20.6 秒后崩溃：`AttributeError: 'NoneType' object has no attribute 'tx'`（`place_floating_parts`） |
| 同上 | `auto_stub` | 8.6 秒后同样崩溃 |
| 去掉 6 个不连线的元件，57 个，单页 | 默认 | 900 秒超时（统一超时先发 CTRL_BREAK，进程正常结束） |
| 只取 pic_sockets 页的 7 个元件 | 默认 | 14 秒后 `RoutingFailure` |
| 同上 | `auto_stub` | 26.8 秒成功 |
| 只取根页的 50 个元件 | `auto_stub` | 31.9 秒成功 |
| 全部 63 个元件 | labels 模式（每个引脚只挂网络标签，不连线） | 6.3 秒成功 |
| LED，3 个元件 | 默认 | 成功（冒烟） |

`auto_stub` 是 schematizer 自带的选项：连不好线的网络改用全局标签，再用 KiCad ERC 反复修正；
它的文档建议按功能块分成 5–15 个元件的子电路。

## 结论

1. 默认模式只适合很小的电路。
2. 打开 `auto_stub` 后，单页 50 个元件 32 秒画完。
3. 完全不连线的元件（安装孔这类）会让 schematizer 崩溃，属于上游 bug。
4. 重画后 ERC 的错误主要来自网表转 IR 时丢掉的信息（不接的引脚没有 NC 标记、电源网络没有标 external_supply），不是画图本身的问题。

## 可读性对照（PDF，等用户看）

`runs/m0-pic-20261031-161138/` 下：`original.pdf`（示例原图）、`sheet-root.pdf`（根页 50 个元件，auto_stub）、
`sheet-pic_sockets.pdf`（7 个元件，auto_stub）、`all-labels.pdf`（63 个元件，labels 模式）。
