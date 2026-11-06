# KiCad symbol subset (test fixture)

These files are an excerpt of the official KiCad symbol libraries, included so
the generator tests run without a KiCad installation.

| File | Symbols | Source |
|---|---|---|
| `Device.kicad_sym` | `R`, `LED`, `C`, `C_Polarized`, `L`, `Crystal`, `Polyfuse`, `D` | KiCad 10.0.8 `/usr/share/kicad/symbols/Device.kicad_sym` (Docker image `kicad/kicad:10.0.8`) |
| `Connector_Generic.kicad_sym` | `Conn_01x02`, `Conn_01x03`, `Conn_01x04`, `Conn_01x05` | KiCad 10.0.8 `/usr/share/kicad/symbols/Connector_Generic.kicad_sym` |
| `Interface_CAN_LIN.kicad_sym` | `SN65HVD230` | KiCad 10.0.8 `/usr/share/kicad/symbols/Interface_CAN_LIN.kicad_sym` (an IC with a datasheet link, for datasheet tests) |
| `Regulator_Linear.kicad_sym` | `AP1117-15`, `AMS1117-3.3`, `L7805`, `LM1117MP-3.3`, `AP2204K-1.5`, `AP2112K-3.3` | KiCad 10.0.8 `/usr/share/kicad/symbols/Regulator_Linear.kicad_sym` (`AMS1117-3.3` and `AP2112K-3.3` are derived; `LM1117MP-3.3` is the I2C sensor sample board's regulator, `AP2112K-3.3` the USB-C board's) |
| `Amplifier_Operational.kicad_sym` | `LM2904`, `LM358`, `MCP6L91T-EOT`, `MCP6001-OT` | KiCad 10.0.8 (a multi-unit symbol: two op-amps and a power unit; `LM358` and `MCP6001-OT` are derived) |
| `MCU_Microchip_ATmega.kicad_sym` | `ATmega48PV-10A`, `ATmega328P-A` | KiCad 10.0.8 (stacked GND/VCC pins; `ATmega328P-A` is derived) |
| `Diode.kicad_sym` | `1N5820`, `1N5822`, `1N4001`, `1N4007`, `SB120`, `SS34`, `SM6T6V8A`, `SMAJ24A`, `1N4148W`, `SMAJ5.0A`, `SM712_SOT23`, `BAT54S` | KiCad 10.0.8 (`1N5822`, `1N4007`, `SS34`, `SMAJ24A` and `SMAJ5.0A` are derived; `SM712_SOT23` and `BAT54S` are three-pin parts) |
| `Connector.kicad_sym` | `USB_C_Receptacle_PowerOnly_6P` | KiCad 10.0.8 (the USB-C power board) |
| `Isolator.kicad_sym` | `PC817` | KiCad 10.0.8 (pins have no names: the checks use a pin-role table) |
| `Relay.kicad_sym` | `SANYOU_SRD_Form_C` | KiCad 10.0.8 (pins have no names) |
| `Transistor_FET.kicad_sym` | `Q_NMOS_GSD`, `AO3400A` | KiCad 10.0.8 (`AO3400A` is derived) |
| `Interface_UART.kicad_sym` | `LTC2850xS8`, `MAX3485` | KiCad 10.0.8 (`MAX3485` is derived, with an empty footprint the parent supplies) |
| `Driver_Motor.kicad_sym` | `DRV8871DDA` | KiCad 10.0.8 (the motor driver sample board) |
| `Regulator_Switching.kicad_sym` | `LM2596S-12`, `LM2596S-5` | KiCad 10.0.8 (`LM2596S-5` is derived) |
| `Switch.kicad_sym` | `SW_Push` | KiCad 10.0.8 (the sample board's reset button) |
| `Timer.kicad_sym` | `NE555D`, `NE555P` | KiCad 10.0.8 (the NE555 sample board; `NE555P` is derived) |
| `power.kicad_sym` | `PWR_FLAG`, `GND`, `VCC`, `+5V`, `+3V3`, `+12V`, `+24V` | KiCad 10.0.8 `/usr/share/kicad/symbols/power.kicad_sym` (rails are drawn with power symbols; the generator adds `PWR_FLAG` where power enters through a connector) |

The symbols are unmodified. They were copied with:

```bash
python scripts/extract_symbol_subset.py <KiCad>/symbols/Device.kicad_sym tests/fixtures/kicad_symbols/Device.kicad_sym R LED C C_Polarized L Crystal Polyfuse D
python scripts/extract_symbol_subset.py <KiCad>/symbols/Connector_Generic.kicad_sym tests/fixtures/kicad_symbols/Connector_Generic.kicad_sym Conn_01x02 Conn_01x03 Conn_01x04 Conn_01x05
python scripts/extract_symbol_subset.py <KiCad>/symbols/Amplifier_Operational.kicad_sym tests/fixtures/kicad_symbols/Amplifier_Operational.kicad_sym LM2904 LM358 MCP6L91T-EOT MCP6001-OT
python scripts/extract_symbol_subset.py <KiCad>/symbols/MCU_Microchip_ATmega.kicad_sym tests/fixtures/kicad_symbols/MCU_Microchip_ATmega.kicad_sym ATmega48PV-10A ATmega328P-A
python scripts/extract_symbol_subset.py <KiCad>/symbols/Diode.kicad_sym tests/fixtures/kicad_symbols/Diode.kicad_sym 1N5820 1N5822 1N4001 1N4007 SB120 SS34 SM6T6V8A SMAJ24A 1N4148W SMAJ5.0A SM712_SOT23 BAT54S
python scripts/extract_symbol_subset.py <KiCad>/symbols/Driver_Motor.kicad_sym tests/fixtures/kicad_symbols/Driver_Motor.kicad_sym DRV8871DDA
python scripts/extract_symbol_subset.py <KiCad>/symbols/Regulator_Switching.kicad_sym tests/fixtures/kicad_symbols/Regulator_Switching.kicad_sym LM2596S-12 LM2596S-5
python scripts/extract_symbol_subset.py <KiCad>/symbols/Interface_CAN_LIN.kicad_sym tests/fixtures/kicad_symbols/Interface_CAN_LIN.kicad_sym SN65HVD230
python scripts/extract_symbol_subset.py <KiCad>/symbols/Regulator_Linear.kicad_sym tests/fixtures/kicad_symbols/Regulator_Linear.kicad_sym AP1117-15 AMS1117-3.3 L7805 LM1117MP-3.3 AP2204K-1.5 AP2112K-3.3
python scripts/extract_symbol_subset.py <KiCad>/symbols/Connector.kicad_sym tests/fixtures/kicad_symbols/Connector.kicad_sym USB_C_Receptacle_PowerOnly_6P
python scripts/extract_symbol_subset.py <KiCad>/symbols/Isolator.kicad_sym tests/fixtures/kicad_symbols/Isolator.kicad_sym PC817
python scripts/extract_symbol_subset.py <KiCad>/symbols/Relay.kicad_sym tests/fixtures/kicad_symbols/Relay.kicad_sym SANYOU_SRD_Form_C
python scripts/extract_symbol_subset.py <KiCad>/symbols/Transistor_FET.kicad_sym tests/fixtures/kicad_symbols/Transistor_FET.kicad_sym Q_NMOS_GSD AO3400A
python scripts/extract_symbol_subset.py <KiCad>/symbols/Interface_UART.kicad_sym tests/fixtures/kicad_symbols/Interface_UART.kicad_sym LTC2850xS8 MAX3485
python scripts/extract_symbol_subset.py <KiCad>/symbols/Switch.kicad_sym tests/fixtures/kicad_symbols/Switch.kicad_sym SW_Push
python scripts/extract_symbol_subset.py <KiCad>/symbols/Timer.kicad_sym tests/fixtures/kicad_symbols/Timer.kicad_sym NE555D NE555P
python scripts/extract_symbol_subset.py <KiCad>/symbols/power.kicad_sym tests/fixtures/kicad_symbols/power.kicad_sym PWR_FLAG GND VCC +5V +3V3 +12V +24V
```

`tests/test_sexpr.py` checks that these files survive a parse/write round trip byte for byte.

**License:** the KiCad libraries are licensed under CC-BY-SA 4.0 with the KiCad
exception (see `LICENSE.md` in this folder, copied from
https://gitlab.com/kicad/libraries/kicad-symbols). This excerpt is redistributed
under the same license. The app itself reads symbols from the user's installed
KiCad libraries; it does not ship these files.
