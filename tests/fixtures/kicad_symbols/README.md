# KiCad symbol subset (test fixture)

These files are an excerpt of the official KiCad symbol libraries, included so
the generator tests run without a KiCad installation.

| File | Symbols | Source |
|---|---|---|
| `Device.kicad_sym` | `R`, `LED`, `C` | KiCad 10.0.8 `/usr/share/kicad/symbols/Device.kicad_sym` (Docker image `kicad/kicad:10.0.8`) |
| `Connector_Generic.kicad_sym` | `Conn_01x02` | KiCad 10.0.8 `/usr/share/kicad/symbols/Connector_Generic.kicad_sym` |
| `Interface_CAN_LIN.kicad_sym` | `SN65HVD230` | KiCad 10.0.8 `/usr/share/kicad/symbols/Interface_CAN_LIN.kicad_sym` (an IC with a datasheet link, for datasheet tests) |
| `Regulator_Linear.kicad_sym` | `AP1117-15`, `AMS1117-3.3` | KiCad 10.0.8 `/usr/share/kicad/symbols/Regulator_Linear.kicad_sym` (`AMS1117-3.3` is a derived symbol) |

The symbols are unmodified. They were copied with:

```bash
python scripts/extract_symbol_subset.py <KiCad>/symbols/Device.kicad_sym tests/fixtures/kicad_symbols/Device.kicad_sym R LED C
python scripts/extract_symbol_subset.py <KiCad>/symbols/Connector_Generic.kicad_sym tests/fixtures/kicad_symbols/Connector_Generic.kicad_sym Conn_01x02
python scripts/extract_symbol_subset.py <KiCad>/symbols/Interface_CAN_LIN.kicad_sym tests/fixtures/kicad_symbols/Interface_CAN_LIN.kicad_sym SN65HVD230
python scripts/extract_symbol_subset.py <KiCad>/symbols/Regulator_Linear.kicad_sym tests/fixtures/kicad_symbols/Regulator_Linear.kicad_sym AP1117-15 AMS1117-3.3
```

`tests/test_sexpr.py` checks that these files survive a parse/write round trip byte for byte.

**License:** the KiCad libraries are licensed under CC-BY-SA 4.0 with the KiCad
exception (see `LICENSE.md` in this folder, copied from
https://gitlab.com/kicad/libraries/kicad-symbols). This excerpt is redistributed
under the same license. The app itself reads symbols from the user's installed
KiCad libraries; it does not ship these files.
