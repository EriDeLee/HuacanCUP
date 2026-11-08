"""带单位前缀的数值：spec 里的 "2ms"、"10k"，元件值里的 "100nF"、"4k7"。

约定（和 SPICE 不同的地方只有 m / M）：m = 毫（1e-3），M 和 meg 都是兆（1e6）。
元件值 "1M" 在原理图里就是 1 MΩ；毫欧要写 "1m" 或 "0.001"。

支持的写法：
- 普通数字和科学计数法：330、0.01、1e-6
- SI 前缀：f p n u µ m k K M meg G T，后面可以跟单位字母（F H Ω ohm V A s Hz W），单位字母只做校验
- RKM 记法：4k7 = 4.7 k，2R2 = 2.2，1M5 = 1.5 M
"""

from __future__ import annotations

import math
import re

_PREFIX = {
    "f": 1e-15,
    "p": 1e-12,
    "n": 1e-9,
    "u": 1e-6,
    "µ": 1e-6,
    "μ": 1e-6,
    "m": 1e-3,
    "k": 1e3,
    "K": 1e3,
    "M": 1e6,
    "meg": 1e6,
    "MEG": 1e6,
    "Meg": 1e6,
    "G": 1e9,
    "T": 1e12,
}
_UNITS = ("hz", "ohm", "Ω", "f", "h", "v", "a", "s", "w", "r")
_NUMBER = r"[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?"
# 数字 [前缀] [单位]；前缀里 meg 要排在 m 前面
_PLAIN = re.compile(rf"^({_NUMBER})\s*(meg|MEG|Meg|[fpnuµμmkKMGT])?\s*([A-Za-zΩ]*)$")
# 4k7、2R2、1M5：前缀（或 R）夹在两段数字中间当小数点
_RKM = re.compile(r"^(\d+)(meg|MEG|Meg|[RrfpnuµμmkKMGT])(\d+)\s*([A-Za-zΩ]*)$")


class UnitError(ValueError):
    pass


def _check_unit(unit: str, text: str) -> None:
    if unit and unit.lower() not in _UNITS and unit not in _UNITS:
        raise UnitError(f"不认识的单位 {unit!r}：{text!r}")


def parse_si(value: float | int | str) -> float:
    """ "2ms" → 0.002；"4k7" → 4700；数字原样返回（转成 float）。"""
    if isinstance(value, bool):
        raise UnitError(f"不是数值：{value!r}")
    if isinstance(value, (int, float)):
        number = float(value)
    else:
        text = value.strip()
        rkm = _RKM.match(text)
        if rkm is not None:
            whole, prefix, frac, unit = rkm.groups()
            _check_unit(unit, text)
            scale = 1.0 if prefix in "Rr" else _PREFIX[prefix]
            number = float(f"{whole}.{frac}") * scale
        else:
            plain = _PLAIN.match(text)
            if plain is None:
                raise UnitError(f"不是带单位前缀的数值：{value!r}")
            digits, prefix, unit = plain.groups()
            # "10m" 里的 m 是前缀；"10ms" 里 m 也是前缀、s 是单位；"5V" 没有前缀，V 是单位。
            # 前缀集合里没有 F、H、V、A、s、W、R，所以这些单位字母不会被当成前缀。
            _check_unit(unit, text)
            number = float(digits) * (_PREFIX[prefix] if prefix else 1.0)
    if not math.isfinite(number):
        raise UnitError(f"不是有限的数值：{value!r}")
    return number


def format_spice(value: float) -> str:
    """数值 → SPICE 网表里的写法：最短且能还原的科学计数法，同样的输入永远得到同样的文本。"""
    if value == 0:
        return "0"
    return f"{value:.9g}"
