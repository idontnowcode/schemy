# 예제: LED + 직렬저항 + 2핀 커넥터

`aics generate`로 만든 KiCad 10 프로젝트입니다. 부품은 J1(커넥터), R1(330Ω), D1(LED) 세 개이고, Net은 VIN, LED_A, GND입니다.
입력 IR은 [`tests/fixtures/circuits/led_resistor_connector.json`](../../tests/fixtures/circuits/led_resistor_connector.json)입니다.

## KiCad에서 열기

1. KiCad 10을 실행합니다.
2. **파일 → 프로젝트 열기**에서 `led-resistor-connector.kicad_pro`를 선택합니다. 탐색기에서 `.kicad_pro`를 더블클릭해도 됩니다.
3. 프로젝트 관리자에서 회로도 `led-resistor-connector.kicad_sch`를 엽니다.

> `.kicad_sch`만 단독으로 열지 마세요. 단독으로 열면 KiCad가 프로젝트 없이 동작합니다.
> 이때 다른 프로젝트의 라이브러리 설정이 섞여서, 이 회로도와 관계없는 라이브러리(예: `mipi_phy`)를 찾을 수 없다는 경고가 뜰 수 있습니다.
> 이 회로도는 쓰는 심볼 3개를 파일 안에 담고 있어서 외부 라이브러리 없이 열립니다.

## 주의

- 이 폴더의 `.kicad_sch`는 테스트 기준 파일(golden)이기도 합니다. KiCad에서 **저장**하면 테스트가 실패합니다.
  편집해 보려면 폴더를 다른 곳에 복사하거나, 아래 명령으로 새로 생성해서 여세요. 원래대로 되돌리려면 `git checkout -- examples/`를 실행합니다.
- KiCad가 프로젝트를 닫을 때 `.kicad_pro`를 다시 쓰거나 `.kicad_prl`, 백업 폴더를 만들 수 있습니다. `.kicad_prl`과 백업 폴더는 git에서 제외되어 있고, `.kicad_pro` 변경은 테스트에 영향을 주지 않습니다.
- 상태는 **unverified**입니다. ERC 통과와 Netlist 일치는 자동 검사 결과일 뿐이며, 저항값 같은 설계 검토는 하지 않았습니다.

## 다시 생성하기

```bash
uv run aics generate tests/fixtures/circuits/led_resistor_connector.json \
    --out my-led-project --project-name led-resistor-connector \
    --symbol-dir tests/fixtures/kicad_symbols --check
```
