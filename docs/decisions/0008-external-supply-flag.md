# ADR-0008 IR 里标出「电从板外进来」的网络，原理图只给这些网络放 PWR_FLAG

- 状态：已采纳（2026-10-31，用户决定）
- 影响：PLAN §5.2 design.json 的 `nets[]`

## 现象

schematizer 把名字像电源的网络（GND、VCC、+5V 等）画成电源符号。KiCad 的电源符号引脚是「电源输入」，
ERC 要求这类网络上有「电源输出」来驱动。LED 板的电从 J1 排针进来，排针脚是普通引脚，所以 ERC 报
`power_pin_not_driven`。

## 决定

- `nets[]` 增加可选字段 `external_supply`（默认 `false`）：这个网络的电由板外经连接器送进来。
- 发射器（`eda_agent/emit/skidl_emit.py`）只给 `external_supply: true` 的网络各放一个 `power:PWR_FLAG`。
- 不自动给「没有驱动的电源网络」加标记：那样会把「忘了放稳压器」这类真错误一起盖住。

## 验证

LED 板给 VIN、GND 标上之后，ERC 0 错误（2026-10-31 冒烟 `smoke-led-20261031-154206`）。
