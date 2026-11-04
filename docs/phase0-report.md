# Phase 0 작업 보고 (2026-11-03)

기획서 16장 "Claude Code 첫 실행 프롬프트"의 8단계를 기준으로 정리했다.

## 1. 환경 탐지

| 항목 | 결과 |
|---|---|
| 개발 환경 | Claude Code 클라우드 컨테이너. Ubuntu 24.04.5 LTS, x86_64 |
| Python | 3.13.18 (uv 0.11.32). 프로젝트 요구 버전은 `>=3.12` |
| Node.js | v22.23.4 / npm 10.9.9 (Electron은 Phase 1에서 사용) |
| Docker | 29.9.2 (데몬을 직접 띄워 사용) |
| KiCad (native) | **설치되어 있지 않음** |
| KiCad 설치 경로 조사 | Ubuntu apt에는 7.0.11만 있음(`sch erc` 없음). KiCad PPA(launchpad), downloads.kicad.org, flathub은 네트워크 정책으로 차단됨 |
| 사용한 KiCad | 공식 Docker 이미지 `kicad/kicad:10.0.8`. Docker Hub가 429(요청 제한)를 반환해 `mirror.gcr.io`에서 받음. Debian 13 기반, `kicad-cli version` = **10.0.8** |
| 목표 플랫폼 | Windows 11. **이번에는 확인하지 못함** |

## 2. ADR

- [ADR-001](adr/ADR-001-eda-generation-strategy.md): 직접 생성(B) **채택**. A안(SKiDL 3.0.0 + schematizer 0.2.0)은 2026-11-04 실험에서 ERC·결정성 기준 미달.
- [ADR-002](adr/ADR-002-kicad-version.md): KiCad 10.0.x(파일 형식 20260306) **채택**.

## 3~5. IR 모델, fixture, 생성기

- `circuit_core/models.py`: `CircuitIR`, `Component`, `Pin`, `Net`, `NetNode`, `Placement`, `Evidence`, `VerificationStatus`.
- `circuit_core/validation.py`: L0/L1 검사. 결과는 API 오류 계약 형식(`error_code`, `message`, `affected_component_ids`, `recovery_hint`)을 따른다.
- `tests/fixtures/circuits/led_resistor_connector.json`: J1(Conn_01x02) – R1(330) – D1(LED). Net은 VIN, LED_A, GND.
- `kicad_adapter/schematic.py`: KiCad 10 `.kicad_sch` 생성기.

**발견한 점**: 기획서 6장 IR 예시는 LED 핀을 `["A","K"]`로 적었지만 KiCad `Device:LED`의 핀 **번호**는 `1`(K), `2`(A)다. 기획서 원칙대로 번호로 저장하도록 했다. 이름을 번호 자리에 넣거나 A/K를 뒤바꾸면 생성을 거부한다(테스트 있음).

## 6~7. KiCad CLI 자동 테스트

```text
$ AICS_KICAD_DOCKER_IMAGE=kicad/kicad:10.0.8 uv run pytest
75 passed in 31.98s
```

```text
$ uv run pytest          # KiCad 없이
69 passed, 6 skipped     # skip 사유: "kicad-cli not available ... KiCad checks are pending, not passed"
```

KiCad 통합 테스트(`tests/test_kicad_integration.py`, 6개, 모두 실제 kicad-cli 10.0.8로 실행됨):

| 테스트 | 확인 내용 |
|---|---|
| `test_kicad_version_is_10` | kicad-cli 버전이 10.x인지 |
| `test_led_circuit_end_to_end` | ERC 오류 0·경고 0, Netlist = IR, SVG 생성 |
| `test_kicad_round_trip_preserves_file` | KiCad가 파일을 로드하고 다시 저장해도 generator 2줄 외에는 바이트 단위로 같음 |
| `test_every_rotation_connects` | R과 LED를 0/90/180/270으로 회전한 8개 부품 링에서 Netlist = IR |
| `test_erc_reports_real_problems` | LED 캐소드를 끊으면 ERC가 `pin_not_connected`를 보고함(검사가 형식적이지 않음을 확인) |
| `test_example_project_opens_cleanly` | `examples/` 예제를 `.kicad_pro`와 함께 열었을 때 ERC 위반 0, Netlist = IR |

Netlist 비교 단위 테스트(`tests/test_netlist.py`)는 KiCad 10.0.8이 실제로 내보낸 Netlist 파일을 fixture로 쓴다.

## 8. README

실행 명령, kicad-cli 연결 방법, 검증 상태의 의미, 제약 사항을 [README](../README.md)에 정리했다.

## Phase 0 Gate 판정

> Gate: Golden test 회로가 열리고 의도한 Netlist와 일치.

- kicad-cli 기준으로는 **충족**: 로드, ERC, Netlist 일치, round-trip 모두 통과.
- **GUI 확인 (2026-11-04, 사용자 PC)**: 회로도가 KiCad에서 열림. 단 `.kicad_sch`를 단독으로 열면 이 파일과 관계없는 라이브러리(`mipi_phy`, `mipi_csi`)를 찾을 수 없다는 경고가 떴고, 프로젝트로 열면 경고가 없었음.
  - 조치: 단독 파일이던 `tests/golden`을 `.kicad_pro`가 포함된 `examples/led-resistor-connector/`로 교체. 생성되는 `.kicad_pro`를 KiCad 10 새 프로젝트 템플릿과 같게 맞춤. README에 프로젝트로 여는 방법을 적음.
- **A/B 평가 (2026-11-04)**: SKiDL 3.0.0 + schematizer 0.2.0을 같은 IR로 실험. Netlist는 일치했으나 ERC 오류 1건과 비결정적 출력으로 통과 기준 미달 → ADR-001 B안 채택(Accepted). 기록: `experiments/adr001_option_a/`.
- **판정: Phase 0 완료.**

## 미해결 위험

1. **Windows 미검증**: kicad-cli 기본 경로, 심볼 경로, 경로 인코딩, 줄바꿈을 아직 Windows에서 실행해 보지 않았다.
2. **GUI 확인은 1회뿐**: 사용자 PC에서 열리는 것만 확인했다. 편집과 저장 후 다시 생성하는 흐름은 아직 확인하지 않았다. `.kicad_sch` 단독 열기는 경고가 생기므로 프로젝트로 열도록 안내한다.
3. **가독성**: label stub 방식이라 직접 배선이 없다. 부품이 많아지면 읽기 어려워진다(ADR-001).
4. **미지원 심볼 기능**: `extends`, 다중 유닛, 전원 심볼, 미러, 계층 시트. MCU 회로(Phase 3)에는 다중 유닛과 전원 심볼이 꼭 필요하다.
5. **KiCad 버전 고정**: KiCad 9 사용자는 파일을 열 수 없다(ADR-002, 결정 필요).
6. **Docker runner**: uid가 0이나 1000이 아닌 사용자로 실행하면 컨테이너 HOME 쓰기 권한 문제가 생길 수 있다. 확인하지 않았다.
7. **ERC는 L2 검사일 뿐이다**: 저항값 330Ω, LED 정격, 입력 전압 같은 L3 공학 검토는 하지 않았다. fixture의 상태는 `unverified`로 유지한다.

## 다음 단계 제안

1. **결정 필요(기획서 18장)**: KiCad 버전(10.0.x 제안), Windows 단일 플랫폼 여부, LLM 공급자와 API 키 입력 방식.
2. Windows 11에 KiCad 10.0을 설치하고 `uv run pytest -m kicad`를 실행한 뒤, 생성 파일을 GUI로 열어 본다.
3. ADR-001 A안 실험(1~2일)을 하고 ADR을 확정한다.
4. 생성기를 확장한다: 전원 심볼과 PWR_FLAG, 다중 유닛, no-connect 표시. 이것들은 MCU 회로의 전제 조건이다.
5. Phase 1 착수: 프로젝트 CRUD(`projects/<id>/project.json`), Markdown/JSON exporter, FastAPI 골격, Electron 셸.
