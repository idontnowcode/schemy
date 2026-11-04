import pytest

from circuit_core.parts import (
    category_from_text,
    category_of,
    normalize_mpn,
    package_key,
    packages_match,
    parse_value,
    values_equal,
)


@pytest.mark.parametrize(
    "text, value",
    [("330", 330), ("330R", 330), ("330Ω", 330), ("4k7", 4700), ("4.7k", 4700), ("4.7 kΩ", 4700), ("100 nF", 1e-7),
     ("10K", 10_000), ("1M", 1e6), ("0R", 0), ("4R7", 4.7), ("100n", 1e-7), ("100nF", 1e-7), ("0.1uF", 1e-7),
     ("0.1µF", 1e-7), ("1u0", 1e-6), ("10uH", 1e-5), ("2.2mH", 2.2e-3), ("10k 1%", 10_000), ("10k/0.1W", 10_000),
     ("LED", None), ("", None), (None, None), ("1,5k", 1500)],
)
def test_parse_value(text, value):
    got = parse_value(text)
    if value is None:
        assert got is None
    else:
        assert got == pytest.approx(value)


def test_values_equal():
    assert values_equal("4k7", "4700")
    assert values_equal("100n", "0.1uF")
    assert not values_equal("100n", "10n")
    assert values_equal("LED", "led")  # falls back to text
    assert not values_equal(None, "1k")


@pytest.mark.parametrize(
    "text, category",
    [("저항", "resistor"), ("칩저항", "resistor"), ("Resistor", "resistor"), ("MLCC", "capacitor"),
     ("콘덴서", "capacitor"), ("전해콘덴서", "capacitor_polarized"), ("Tantalum cap", "capacitor_polarized"),
     ("LED", "led"), ("발광다이오드", "led"), ("쇼트키 다이오드", "diode"), ("커넥터", "connector"),
     ("Pin Header", "connector"), ("IC", "ic"), ("LDO 레귤레이터", "ic"), ("c", "capacitor"), ("뭔가", None)],
)
def test_category_from_text(text, category):
    assert category_from_text(text) == category


def test_category_of_symbol_and_reference():
    assert category_of("Device:R") == "resistor"
    assert category_of("Device:R_Small") == "resistor"
    assert category_of("Device:C_Polarized") == "capacitor_polarized"
    assert category_of("Device:LED") == "led"
    assert category_of("Device:D_Schottky") == "diode"
    assert category_of("Connector_Generic:Conn_01x02") == "connector"
    assert category_of("MCU_ST:STM32F103C8Tx", "U1") == "ic"
    assert category_of(None, "LED3") == "led"
    assert category_of(None, "XX1") is None


def test_package_keys():
    assert package_key("Resistor_SMD:R_0603_1608Metric").size == "0603"
    assert package_key("0603").size == "0603"
    assert package_key("SOT-23-5").size == "SOT235"
    assert package_key("Package_TO_SOT_SMD:SOT-23-5").size == "SOT235"
    hdr = package_key("Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical")
    assert (hdr.size, hdr.pitch) == ("1x02", 2.54)
    assert package_key(None) is None


def test_packages_match():
    assert packages_match(package_key("R_0603_1608Metric"), package_key("0603")) is True
    assert packages_match(package_key("R_0603_1608Metric"), package_key("0805")) is False
    assert packages_match(package_key("PinHeader_1x02_P2.54mm"), package_key("1x02 2.0mm")) is False
    assert packages_match(package_key("PinHeader_1x02_P2.54mm"), package_key("1x02")) is True
    assert packages_match(package_key("0603"), package_key("custom")) is None
    assert packages_match(None, package_key("0603")) is None


def test_normalize_mpn():
    assert normalize_mpn(" rc0603fr-07330rl ") == normalize_mpn("RC0603FR07330RL")
    assert normalize_mpn("") is None
