from __future__ import annotations

import pytest

from eda_agent.units import UnitError, format_spice, parse_si


@pytest.mark.parametrize(
    "text, expected",
    [
        ("330", 330.0),
        ("0.01", 0.01),
        ("1e-6", 1e-6),
        ("10k", 1e4),
        ("4.7k", 4700.0),
        ("4k7", 4700.0),
        ("2R2", 2.2),
        ("1M5", 1.5e6),
        ("1M", 1e6),
        ("1MEG", 1e6),
        ("2.2meg", 2.2e6),
        ("1m", 1e-3),
        ("2ms", 2e-3),
        ("100nF", 1e-7),
        ("10uF", 1e-5),
        ("10µF", 1e-5),
        ("22pF", 2.2e-11),
        ("1kHz", 1e3),
        ("1MHz", 1e6),
        ("5V", 5.0),
        ("20A", 20.0),
        ("10s", 10.0),
        ("3.3V", 3.3),
        (7, 7.0),
        (2.5, 2.5),
        (" 1k ", 1e3),
        ("-1.5m", -1.5e-3),
    ],
)
def test_parse_si(text: str | float, expected: float) -> None:
    assert parse_si(text) == pytest.approx(expected, rel=1e-12)


@pytest.mark.parametrize("text", ["", "abc", "1x", "1kk", "1k2k", "5 volts", "nan", "inf", True])
def test_parse_si_rejects_what_is_not_a_quantity(text: object) -> None:
    with pytest.raises(UnitError):
        parse_si(text)  # type: ignore[arg-type]


def test_m_is_milli_and_capital_m_is_mega() -> None:
    """和 SPICE 不一样的地方：SPICE 里 m、M 都是毫。"""
    assert parse_si("1m") == pytest.approx(1e-3)
    assert parse_si("1M") == pytest.approx(1e6)


def test_format_spice_is_short_and_stable() -> None:
    assert format_spice(0) == "0"
    assert format_spice(4700.0) == "4700"
    assert format_spice(1e-7) == "1e-07"
    assert format_spice(0.001) == "0.001"
    assert float(format_spice(1 / 3)) == pytest.approx(1 / 3, rel=1e-8)
    assert format_spice(parse_si("4k7")) == format_spice(parse_si("4.7k"))
