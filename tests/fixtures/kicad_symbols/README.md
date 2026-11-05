# KiCad symbol subset (test fixture)

These files are an excerpt of the official KiCad symbol libraries, included so
the generator tests run without a KiCad installation.

| File | Symbols | Source |
|---|---|---|
| `Device.kicad_sym` | `R`, `LED`, `C`, `C_Polarized`, `L`, `Crystal` | KiCad 10.0.8 `/usr/share/kicad/symbols/Device.kicad_sym` (Docker image `kicad/kicad:10.0.8`) |
| `Connector_Generic.kicad_sym` | `Conn_01x02`, `Conn_01x03`, `Conn_01x04` | KiCad 10.0.8 `/usr/share/kicad/symbols/Connector_Generic.kicad_sym` |
| `Interface_CAN_LIN.kicad_sym` | `SN65HVD230` | KiCad 10.0.8 `/usr/share/kicad/symbols/Interface_CAN_LIN.kicad_sym` (an IC with a datasheet link, for datasheet tests) |
| `Regulator_Linear.kicad_sym` | `AP1117-15`, `AMS1117-3.3` | KiCad 10.0.8 `/usr/share/kicad/symbols/Regulator_Linear.kicad_sym` (`AMS1117-3.3` is a derived symbol) |
| `Amplifier_Operational.kicad_sym` | `LM2904`, `LM358` | KiCad 10.0.8 (a multi-unit symbol: two op-amps and a power unit; `LM358` is derived) |
| `MCU_Microchip_ATmega.kicad_sym` | `ATmega48PV-10A`, `ATmega328P-A` | KiCad 10.0.8 (stacked GND/VCC pins; `ATmega328P-A` is derived) |
| `Diode.kicad_sym` | `1N5820`, `1N5822` | KiCad 10.0.8 (`1N5822` is derived) |
| `Regulator_Switching.kicad_sym` | `LM2596S-12`, `LM2596S-5` | KiCad 10.0.8 (`LM2596S-5` is derived) |
| `Switch.kicad_sym` | `SW_Push` | KiCad 10.0.8 (the sample board's reset button) |
| `Timer.kicad_sym` | `NE555D`, `NE555P` | KiCad 10.0.8 (the NE555 sample board; `NE555P` is derived) |
| `power.kicad_sym` | `PWR_FLAG`, `GND`, `VCC`, `+5V`, `+3V3`, `+12V`, `+24V` | KiCad 10.0.8 `/usr/share/kicad/symbols/power.kicad_sym` (rails are drawn with power symbols; the generator adds `PWR_FLAG` where power enters through a connector) |

The symbols are unmodified. They were copied with:

```bash
python scripts/extract_symbol_subset.py <KiCad>/symbols/Device.kicad_sym tests/fixtures/kicad_symbols/Device.kicad_sym R LED C C_Polarized L Crystal
python scripts/extract_symbol_subset.py <KiCad>/symbols/Connector_Generic.kicad_sym tests/fixtures/kicad_symbols/Connector_Generic.kicad_sym Conn_01x02 Conn_01x03 Conn_01x04
python scripts/extract_symbol_subset.py <KiCad>/symbols/Amplifier_Operational.kicad_sym tests/fixtures/kicad_symbols/Amplifier_Operational.kicad_sym LM2904 LM358
python scripts/extract_symbol_subset.py <KiCad>/symbols/MCU_Microchip_ATmega.kicad_sym tests/fixtures/kicad_symbols/MCU_Microchip_ATmega.kicad_sym ATmega48PV-10A ATmega328P-A
python scripts/extract_symbol_subset.py <KiCad>/symbols/Diode.kicad_sym tests/fixtures/kicad_symbols/Diode.kicad_sym 1N5820 1N5822
python scripts/extract_symbol_subset.py <KiCad>/symbols/Regulator_Switching.kicad_sym tests/fixtures/kicad_symbols/Regulator_Switching.kicad_sym LM2596S-12 LM2596S-5
python scripts/extract_symbol_subset.py <KiCad>/symbols/Interface_CAN_LIN.kicad_sym tests/fixtures/kicad_symbols/Interface_CAN_LIN.kicad_sym SN65HVD230
python scripts/extract_symbol_subset.py <KiCad>/symbols/Regulator_Linear.kicad_sym tests/fixtures/kicad_symbols/Regulator_Linear.kicad_sym AP1117-15 AMS1117-3.3
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
