# ADR-001: Circuit IR → KiCad 회로도 생성 방식

- 상태: **채택(Accepted)** — B안(직접 생성기). 2026-11-04 A안 실험 결과 반영.
- 날짜: 2026-11-03 (제안), 2026-11-04 (채택)
- 관련: 기획서 4장(기술 스택), 11장(EDA 생성 전략 비교), 12장(Phase 0), 13장(위험)

## 배경

승인된 Circuit IR(JSON)을 KiCad `.kicad_sch` 회로도로 바꿔야 한다. 요구사항은 다음과 같다.

- **재현성**: 같은 IR이면 같은 파일이 나와야 한다(FR-06, Phase 1 Gate).
- **검증 가능성**: 만든 파일의 Netlist가 IR 연결과 일치함을 자동으로 증명할 수 있어야 한다(개발 규칙 7).
- **안전성**: 잘못된 핀 번호나 존재하지 않는 심볼이 파일에 들어가면 안 된다(위험: LLM의 잘못된 핀 번호).
- **편집 가능성**: 사용자가 KiCad에서 열어 계속 편집할 수 있어야 한다(MVP 완료 기준).

## 후보

### A. SKiDL + schematizer

2026-11-03에 패키지 메타데이터와 소스를 읽어 확인한 사실이다. 코드를 실행해 보지는 않았다.

- SKiDL 3.0.0 (2026-10-16 릴리스): `generate_schematic()`가 범용 netlist를 만들어 별도 패키지 **schematizer**에 넘긴다. 3.0.0 변경 이력에 따르면 회로도 배치·배선·파일 쓰기는 모두 schematizer가 맡는다.
- schematizer 0.2.0 (MIT): 대상으로 `kicad6`부터 `kicad10`까지 지원한다. 단 `kicad10`을 대상으로 해도 **파일 버전 20250114(KiCad 9 형식)**로 쓴다. 소스 주석에는 "KiCad 10이 그대로 읽는다"고 되어 있다.
- 자동 배치·배선이 들어 있어 사람이 보기 좋은 회로도를 만들 가능성이 있다.
- 의존성: `schematizer`, `kinet2pcb`, `simp_sexp`, `inspice`, `graphviz`, `ply`, `rich`, `deprecation`.
- 둘 다 막 나온 버전이다(0.2.0, 메이저 3.0.0). 출력 형식과 동작이 앞으로 바뀔 여지가 크다.

### B. 직접 `.kicad_sch` 생성기 (이 저장소의 `kicad_adapter`)

- IR을 읽어 KiCad 10 형식(`version 20260306`)의 S-expression을 직접 쓴다.
- 심볼 정의와 핀 좌표는 **설치된 KiCad 라이브러리 파일에서 읽는다**. 추측하는 값은 없다.
- 연결 방식(PoC): 핀마다 짧은 wire stub을 긋고 그 끝에 Net 이름 label을 단다. 연결이 배치와 무관하게 IR Net으로만 정해진다.
- UUID는 `uuid5(project_id, IR id)`로 만든다. 같은 IR이면 바이트 단위로 같은 파일이 나온다.

## 비교 (기획서 11장 기준)

| 기준 | A. SKiDL + schematizer | B. 직접 생성 (PoC 결과) |
|---|---|---|
| 파일 열림 | 미확인 (실험 필요) | ✅ kicad-cli 10.0.8이 로드하고 다시 저장함. 차이는 generator 2줄뿐 |
| ERC | 미확인 | ✅ LED 회로: 오류 0, 경고 0 |
| Netlist 정확성 | 미확인. 자동 배선이 연결을 바꾸지 않는지 확인 필요 | ✅ 추출 Netlist = IR (회전 0/90/180/270 모두 포함) |
| 배치 가독성 | 자동 배치·배선 제공(품질 미확인) | ⚠️ label stub 방식이라 직접 배선이 없음. 블록 템플릿이나 배치 힌트로 보완 필요 |
| 버전 안정성 | KiCad 9 형식으로 씀. 외부 패키지 업데이트에 따라 출력이 변함 | 지원 버전(10.0.x)을 고정하고, golden 파일과 round-trip 테스트로 형식 변화를 감지 |
| 변경 시 결정성 | 배치 알고리즘의 결정성 미확인 | ✅ uuid5와 정렬로 같은 IR → 같은 파일 |
| 핀맵 안전성 | SKiDL이 라이브러리를 읽음. IR 핀맵과의 대조는 따로 구현해야 함 | ✅ 라이브러리 핀 번호·이름과 대조하고, 틀리면 파일을 쓰지 않음 |
| 개발 비용 | 낮음(배치·배선 포함) | 중간. 기본 생성은 끝남. 배선·다중 유닛·전원 심볼은 직접 구현해야 함 |

## 결정

1. **생성 경로는 B(직접 생성기)로 한다.** 재현성, 핀맵 안전성, Netlist 일치를 테스트로 증명할 수 있다.
2. **A는 채택하지 않는다**(생성기로도, 배치 보조 도구로도). 아래 실험에서 통과 기준 셋 중 ERC와 결정성을 통과하지 못했다.
3. 배치 가독성은 Phase 3의 검증된 블록 템플릿과 배치 힌트(`layout`)로 개선한다. A안은 SKiDL/schematizer가 결정적 배치를 지원하게 되면 다시 평가할 수 있다.

## A안 실험 (2026-11-04 수행)

1. 격리된 venv에 `skidl==3.0.0`, `schematizer==0.2.0`을 설치하고 KiCad 10.0.8 라이브러리 경로를 지정한다.
2. `tests/fixtures/circuits/led_resistor_connector.json`을 SKiDL 코드로 옮기는 얇은 변환기를 만든다(IR → SKiDL Part/Net).
3. `generate_schematic(tool=KICAD10)`로 회로도를 만든다.
4. B와 같은 검사를 돌린다: `kicad-cli sch erc`, `sch export netlist` 결과를 `compare_with_ir`로 비교, `sch upgrade --force` round-trip.
5. 같은 입력으로 두 번 생성해 결정성(바이트 동일 여부)을 확인한다.
6. 회전 링 회로(`tests/circuits.py`)와 10개 이상 부품 회로에서 SVG를 렌더링해 가독성을 비교한다.
7. 결과를 이 문서에 기록하고 상태를 **Accepted**로 바꾼다.

**통과 기준**: Netlist가 IR과 일치하고, ERC 오류가 0이고, 같은 입력에서 결정적으로 생성되어야 한다. 이 셋 중 하나라도 실패하면 A는 보조 도구로도 쓰지 않는다.

### 실험 결과

상세 기록과 재현 방법: [`experiments/adr001_option_a/`](../../experiments/adr001_option_a/README.md). 1~5단계와 6단계 중 LED 회로 렌더링을 수행했다. 결정성에서 이미 탈락해 10개 이상 부품 회로 비교는 생략했다.

| 통과 기준 | A 결과 |
|---|---|
| Netlist = IR | ✅ 일치 |
| ERC 오류 0 | ❌ 오류 1(`power_pin_not_driven`: GND Net에 전원 심볼을 자동 삽입, PWR_FLAG 없음), 경고 3(`lib_symbol_mismatch`) |
| 결정적 생성 | ❌ 루트 UUID(uuid4)와 배치가 실행마다 바뀜. `random.seed(0)`과 `PYTHONHASHSEED=0`으로 고정해도 106줄 차이 |

부가 관찰: 출력 형식은 KiCad 9(`20250114`)이고 `.kicad_pro`를 만들지 않는다. KiCad 재저장 시 2,200줄 diff. 좌표에 부동소수점 잔여값이 남는다. 배선은 그리지만 이 회로에서는 라벨과 심볼이 겹쳤다.

## 결과와 영향

- KiCad 파일 형식 호환성은 우리가 책임진다. 대응책: 지원 버전을 고정하고(ADR-002), CI에서 golden 파일과 kicad-cli round-trip 테스트를 돌린다.
- 현재 생성기의 미지원 범위: 파생 심볼(`extends`), 다중 유닛 심볼, 미러, 전원 심볼(`power:`), 계층 시트, `.kicad_symdir` 라이브러리. 만나면 오류를 내며 조용히 무시하지 않는다.
- 회로도의 가독성은 label 방식의 한계가 있다. Phase 3의 "검증된 기능 블록 템플릿"이 배치 품질을 담당한다.
