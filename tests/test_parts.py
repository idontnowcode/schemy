import pytest

from circuit_core.parts import (
    category_from_text,
    category_of,
    normalize_mpn,
    package_footprint,
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


@pytest.mark.parametrize(
    "category, package, pins, footprint",
    [("diode", "SOD-123", 2, "Diode_SMD:D_SOD-123"), ("diode", "SMA", 2, "Diode_SMD:D_SMA"),
     ("ic", "SOIC-8", 8, "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm"), ("ic", "SOT-23-5", 5, "Package_TO_SOT_SMD:SOT-23-5"),
     ("connector", "GCT USB4125 6P", 7, "Connector_USB:USB_C_Receptacle_GCT_USB4125-xx-x_6P_TopMnt_Horizontal"),
     ("fuse", "1206", 2, "Fuse:Fuse_1206_3216Metric"),
     ("capacitor_polarized", "6.3x5.4", 2, "Capacitor_SMD:CP_Elec_6.3x5.4"),
     ("connector", "2.54mm 헤더", 3, "Connector_PinHeader_2.54mm:PinHeader_1x03_P2.54mm_Vertical"),
     ("connector", "5.0mm 단자대", 3, "TerminalBlock_Phoenix:TerminalBlock_Phoenix_MKDS-1,5-3_1x03_P5.00mm_Horizontal"),
     # Found in a real-flow run: "5.08mm" became the 5.00 mm part, and a 2x3 ISP header a 1x6 one.
     ("connector", "5.08mm 단자대", 3,
      "TerminalBlock_Phoenix:TerminalBlock_Phoenix_MKDS-1,5-3-5.08_1x03_P5.08mm_Horizontal"),
     ("connector", "2.54mm 2x3 헤더", 6, "Connector_PinHeader_2.54mm:PinHeader_2x03_P2.54mm_Vertical"),
     ("connector", "2.54mm 2열 헤더", 10, "Connector_PinHeader_2.54mm:PinHeader_2x05_P2.54mm_Vertical"),
     ("switch", "SMD Switch:SW_DIP_x02", 4,
      "Button_Switch_SMD:SW_DIP_SPSTx02_Slide_6.7x6.64mm_W8.61mm_P2.54mm_LowProfile"),
     ("switch", "THT DIP", 8, "Button_Switch_THT:SW_DIP_SPSTx04_Slide_9.78x12.34mm_W7.62mm_P2.54mm"),
     ("switch", "SMD", 4, None),
     ("resistor", "0603", 2, "Resistor_SMD:R_0603_1608Metric"), ("ic", "QFN-48", 48, None), ("connector", "", 3, None),
     ("resistor", "2512", 2, "Resistor_SMD:R_2512_6332Metric"),
     ("capacitor", "1210", 2, "Capacitor_SMD:C_1210_3225Metric"),
     ("led", "1210", 2, None), ("fuse", "1812", 2, "Fuse:Fuse_1812_4532Metric"),
     ("crystal", "3225", 4, "Crystal:Crystal_SMD_3225-4Pin_3.2x2.5mm"), ("crystal", "3225", 2, None),
     ("crystal", "2012", 2, "Crystal:Crystal_SMD_2012-2Pin_2.0x1.2mm"),
     ("inductor", "SRN6045TA", 2, "Inductor_SMD:L_Bourns_SRN6045TA"),
     ("connector", "1.27mm 2x05", 10, "Connector_PinHeader_1.27mm:PinHeader_2x05_P1.27mm_Vertical_SMD"),
     ("switch", "TL3342", 2, "Button_Switch_SMD:SW_SPST_TL3342")],
)
def test_package_footprint(category, package, pins, footprint):
    """The package a designer names becomes a KiCad footprint (every name here exists in KiCad 10.0.8's libraries)."""
    assert package_footprint(category, package, pins) == footprint
