# ADR-001 A안 실험: SKiDL 3.0.0 + schematizer 0.2.0

- 날짜: 2026-11-04
- 입력: `examples/led-resistor-connector/circuit.json` (B안과 같은 IR)
- 도구: skidl 3.0.0, schematizer 0.2.0, simp_sexp 0.3.2 (격리 venv, Python 3.13)
- 심볼: KiCad 10.0.8 공식 라이브러리 (`KICAD10_SYMBOL_DIR`)
- 검사: kicad-cli 10.0.8 (Docker `kicad/kicad:10.0.8`), Netlist 비교는 `kicad_adapter.compare_with_ir`

## 결과

| 기준 | A. SKiDL + schematizer | B. 직접 생성기 |
|---|---|---|
| 파일 열림 | ✅ kicad-cli가 로드함 | ✅ |
| 파일 형식 | `version 20250114`(KiCad 9 형식), `.kicad_pro` 없음 | `20260306`(KiCad 10), `.kicad_pro` 함께 생성 |
| Netlist ↔ IR | ✅ 일치 | ✅ 일치 |
| ERC | ❌ 오류 1: `power_pin_not_driven` (GND Net에 전원 심볼 `#PWR001`을 자동 삽입했으나 PWR_FLAG 없음). 경고 3: `lib_symbol_mismatch` (내장 심볼이 KiCad 10 라이브러리와 다름) | ✅ 오류 0, 경고 0 |
| 결정성 | ❌ 실행마다 루트 UUID(uuid4)와 부품 배치(위치·회전)가 바뀜. `random.seed(0)` + `PYTHONHASHSEED=0`으로 고정해도 106줄이 달라짐 | ✅ 같은 IR → 같은 바이트 |
| KiCad 재저장 round-trip | 2,200줄 diff (형식 변환) | generator 2줄만 다름 |
| 좌표 | `106.67999999999999` 같은 부동소수점 잔여값 | 4자리 반올림 |
| 배치 가독성 | 실제 배선을 그림. 단 이 회로에서는 라벨이 심볼과 겹치고 배선이 R1 옆을 가로지름 (`docs/images/adr001_option_a_skidl.png`) | 배선 없이 라벨 연결 (`docs/images/led_resistor_connector.png`) |

## 판정

ADR-001의 통과 기준(Netlist 일치, ERC 오류 0, 결정적 생성) 중 **ERC와 결정성 두 가지를 통과하지 못했다.** 계획에 따라 A는 생성기로도, 배치 보조 도구로도 채택하지 않는다.

## 재현

```bash
uv venv skidl-venv --python 3.13
VIRTUAL_ENV=skidl-venv uv pip install skidl==3.0.0 schematizer==0.2.0
KICAD10_SYMBOL_DIR=<KiCad 10 symbols> skidl-venv/bin/python experiments/adr001_option_a/run_skidl.py \
    examples/led-resistor-connector/circuit.json out ledA
# 이후 kicad-cli sch erc / sch export netlist / sch upgrade --force 로 검사
```
