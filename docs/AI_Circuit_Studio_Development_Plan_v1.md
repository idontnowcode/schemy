# AI Circuit Studio — 앱 개발 기획서 v1.0

- 작성일: 2026-11-04
- 목적: Claude Code에서 개발 착수 가능한 PRD + 기술 설계 + 구현 순서
- 대상: Windows 우선 로컬 데스크톱 앱
- 상태: 초안. 실제 부품 선정과 회로 검증은 별도 승인 필요.

## 1. 제품 목표
사용자가 자연어로 회로 요구사항을 입력하면 AI가 요구사항을 구조화하고, 설계안과 부품 후보를 제시한다. 사용자가 승인한 설계를 정형 Circuit IR로 저장하고, Markdown 문서와 KiCad 회로도를 생성한다. 변경 제안은 diff를 검토·승인한 후 적용한다.

### 비목표 (초기)
- 완전 자동 PCB 레이아웃/라우팅
- 외부 임의 KiCad 파일의 완전한 양방향 편집
- ERC 통과만으로 전기적 안전/제작성 보장
- 데이터시트 미확인 부품의 자동 최종 승인

## 2. 사용자 시나리오
1. 새 프로젝트 생성: STM32 CAN Controller.
2. 요구사항 입력: 24V DC 입력, 5V 2A, 3.3V 0.5A, STM32F103C8T6, SN65HVD230, 선택형 120Ω CAN 종단, SWD, 입력 보호.
3. AI가 모호한 요구사항(24V 입력 범위, 환경, 커넥터, 출력 부하 등)을 식별한다.
4. AI가 기능 블록·부품 후보·설계 근거·검증 대기 항목을 제시한다.
5. 사용자가 승인하면 Circuit IR에 반영하고 Markdown/JSON을 저장한다.
6. EDA Adapter가 KiCad .kicad_sch를 생성하고 KiCad CLI ERC/Netlist/SVG를 실행한다.
7. UI에서 생성 결과, 경고, 검증 상태를 표시한다.
8. 변경 요청 시 변경 제안(diff)을 보여주고 승인 후 버전으로 저장한다.

## 3. MVP 기능 요구사항
| ID | 기능 | 우선순위 | 완료 조건 |
|---|---|---|---|
| FR-01 | 프로젝트 생성/열기/저장 | P0 | 로컬 프로젝트 재열기 시 상태 복원 |
| FR-02 | 요구사항 입력·편집 | P0 | 텍스트와 필드형 사양 저장 |
| FR-03 | AI 요구사항 구조화 | P0 | 스키마 검증 가능한 결과 반환 |
| FR-04 | 설계안 제안 및 승인 | P0 | 승인 전 원본 변경 없음 |
| FR-05 | Circuit IR 관리 | P0 | component/pin/net ID 안정적 유지 |
| FR-06 | Markdown/JSON 내보내기 | P0 | 동일 IR에서 결정적 생성 |
| FR-07 | KiCad 회로도 생성 | P0 | 지원 회로에서 KiCad 파일 열림 |
| FR-08 | ERC·Netlist·SVG 결과 표시 | P0 | 실행 결과와 오류를 UI에서 확인 |
| FR-09 | 변경 diff·버전 이력 | P1 | 승인/취소/롤백 가능 |
| FR-10 | BOM CSV | P1 | ref/value/MPN/footprint 포함 |
| FR-11 | 검증 상태 추적 | P0 | 미확인/검토 필요/검증됨 구분 |
| FR-12 | 데이터시트 첨부 | P1 | 로컬 파일 참조, 출처 기록 |

## 4. 권장 기술 스택
- Electron + React + TypeScript + Vite: 데스크톱 셸 및 UI.
- Python 3.12+ + FastAPI + Pydantic v2: 회로 데이터 모델, 검증, 변환 백엔드.
- OpenAI 또는 호환 LLM API: 요구사항 구조화, 설계안, 변경 제안. Claude에서 개발하더라도 앱의 LLM 공급자는 교체 가능한 인터페이스로 구현.
- KiCad (설치 버전 고정): 공식 파일 형식, CLI ERC, Netlist, SVG. CLI 지원 옵션은 설치 버전에서 확인.
- SQLite: 프로젝트 인덱스, 작업 이력, 승인 이력. 프로젝트 원본은 JSON 및 Markdown 파일.
- React Flow: 기능 블록도에만 사용. 전자회로 편집기 자체의 대체재로 가정하지 않음.
- SKiDL/schematizer: PoC 후보. 설치 버전과 실제 출력 기능을 확인한 후 채택 여부 결정. 직접 .kicad_sch 생성 어댑터와 비교.

## 5. 시스템 구성
```text
Electron / React UI
  ├─ Project Explorer
  ├─ Requirements Editor
  ├─ AI Proposal + Diff Review
  ├─ Block Diagram
  ├─ Schematic Preview (SVG)
  └─ Validation / BOM / History
          │ local HTTP / IPC
Python Application API
  ├─ Project Service
  ├─ AI Orchestrator (structured output only)
  ├─ Circuit IR Service
  ├─ Rules Validator
  ├─ KiCad Adapter
  ├─ Exporter (MD/JSON/BOM)
  └─ Revision Service
          ├─ Project files / SQLite
          ├─ KiCad CLI
          └─ LLM API (user opt-in)
```

## 6. Circuit IR 설계 원칙
- JSON을 기계적 단일 원본(Source of Truth)으로 사용. Markdown은 읽기 쉬운 파생 문서이며 구조화된 코드 블록을 포함할 수 있음.
- component_id, pin_id, net_id, block_id는 변경에 강한 UUID/안정 ID 사용.
- 부품의 논리적 핀과 KiCad 심볼의 물리 핀 번호를 구분.
- 모든 연결은 실제 pin_number 기준으로 저장; `U2.CAN_TX` 같은 의미적 별칭만으로 EDA 연결을 확정하지 않음.
- symbol_library_id, footprint_library_id, manufacturer_part_number, symbol_version, pin_map_evidence 기록.
- 값과 단위, 허용오차, 입력 범위, 검증 상태, 근거 출처를 명시.
- UI 위치와 회로 연결 정보는 분리. 자동 레이아웃으로 Netlist가 변경되어서는 안 됨.

### IR 예시 (설명용, 제작용 아님)
```json
{
  "schema_version": "0.1.0",
  "project_id": "stm32-can-demo",
  "components": [
    {"id":"res-001", "reference":"R1", "value":"330R", "symbol_id":"Device:R", "pins":["1","2"]},
    {"id":"led-001", "reference":"D1", "value":"LED", "symbol_id":"Device:LED", "pins":["A","K"]}
  ],
  "nets": [
    {"id":"net-001", "name":"LED_ANODE", "nodes":[{"component_id":"res-001","pin":"2"},{"component_id":"led-001","pin":"A"}]}
  ],
  "validation": {"status":"unverified"}
}
```
위 예시는 완전한 회로가 아니며 실제 KiCad 심볼 핀 식별자와 매핑 검증이 필요함.

## 7. 데이터 및 프로젝트 구조
```text
AI-Circuit-Studio/
  desktop/                 # Electron + React
  backend/                 # FastAPI
  circuit_core/            # Pydantic models / rules
  kicad_adapter/           # export, ERC, netlist, SVG
  ai_engine/               # prompts, structured outputs, proposals
  tests/                   # unit / golden-file / integration
  templates/               # reviewed circuit blocks
  docs/                    # ADR, schema, specs
  projects/<project-id>/
    project.json
    requirements.md
    circuit.json
    design_notes.md
    datasheets/
    revisions/
    outputs/
      schematic.kicad_sch
      schematic.svg
      bom.csv
      erc.json
```

## 8. API 계약 초안
- `POST /projects` : 프로젝트 생성
- `GET /projects/{id}` : 프로젝트 메타데이터
- `POST /projects/{id}/requirements/parse` : 요구사항 → 구조화 스펙
- `POST /projects/{id}/proposals` : AI 설계 제안 (적용하지 않음)
- `POST /projects/{id}/proposals/{proposal_id}/approve` : 사용자 승인 후 트랜잭션 적용
- `GET /projects/{id}/circuit` : Circuit IR 조회
- `POST /projects/{id}/exports/kicad` : KiCad 파일 생성
- `POST /projects/{id}/validation/erc` : ERC 실행
- `GET /projects/{id}/revisions` : 변경 이력
- `POST /projects/{id}/revisions/{revision_id}/restore` : 확인 후 복원

오류 응답은 error_code, message, affected_component_ids, recovery_hint 필드를 포함. 오래 걸리는 EDA 작업은 job_id와 상태 조회 API를 사용.

## 9. 설계 변경 처리
1. AI가 `add_component`, `remove_component`, `update_property`, `connect`, `disconnect` 등 허용된 연산으로 patch 생성.
2. 서버가 현재 revision과 precondition을 확인.
3. schema/참조/핀/Net 규칙 검증.
4. UI에서 영향 범위와 before/after diff 제공.
5. 승인 시 원자적으로 새 revision 저장.
6. EDA 재생성, Netlist 비교, ERC 실행.
7. 실패하면 기존 revision 보존, 오류 보고.

## 10. 검증 전략
- L0 Schema: 타입/필수 필드/참조 ID/중복 검증.
- L1 Topology: 핀 존재 여부, 미연결, Net 충돌, 연결 그래프 정합성.
- L2 EDA: KiCad CLI ERC, netlist export, schematic export.
- L3 Engineering: 데이터시트 핀맵, 전압·전류 정격, 보호회로, 발열, 전원 시퀀스, EMC/ESD 등 설계 검토.
- L4 Physical: 시뮬레이션·시제품 시험. MVP 범위 밖.

ERC 통과는 설계 적합성의 충분조건이 아님. UI에는 `Not checked`, `Automated checks passed`, `Engineer reviewed`를 분리해 표시. AI가 검증하지 않은 사항을 검증 완료로 표시하지 않음.

## 11. EDA 생성 전략 비교 PoC
A. SKiDL + schematizer: 빠른 초기 평가. 지원 버전, 라이브러리 호환, 배치 품질 확인.
B. 직접 KiCad schematic generator: 제어력 높으나 심볼 인스턴스, 라이브러리 참조, UUID, 좌표, 배선, 버전 호환 구현 비용 큼.

선택 기준: 파일 열림, ERC, Netlist 정확성, 회로 배치 가독성, 버전 안정성, 변경 시 결정성, 개발 비용.

## 12. 단계별 구현 계획 및 게이트
### Phase 0 — 환경/PoC (1~2주 목표)
- Electron/React, Python, KiCad 버전 확인 및 설치.
- 수동 Circuit IR로 저항 + LED + 커넥터 회로 생성.
- KiCad에서 열기, CLI ERC, Netlist, SVG 확인.
- 생성 엔진 A/B 평가, ADR 기록.
- Gate: Golden test 회로가 열리고 의도한 Netlist와 일치.

### Phase 1 — Core MVP (2~4주 목표)
- 프로젝트 CRUD, Circuit IR schema, Markdown/JSON exporter.
- EDA Adapter, validation UI, 생성 파일 경로 관리.
- Gate: 앱 재시작 후 프로젝트 복원, 동일 입력에서 동일 연결 구조 출력.

### Phase 2 — AI Workflow (3~5주 목표)
- 요구사항 구조화, 설계안 생성, 승인, patch/diff, revision.
- Gate: 승인 전 데이터 불변, 거부 시 변경 없음, 수정 후 재검증.

### Phase 3 — Engineering Templates (4~8주 목표)
- 전원부, MCU 최소 회로, CAN 회로 템플릿 확장.
- 실제 데이터시트 기반 핀맵/정격 검토 프로세스.
- Gate: 각 템플릿에 근거와 검증 체크리스트 첨부.

기간은 단독 개발의 잠정 추정치이며 작업 시간과 도구 호환성에 따라 달라짐.

## 13. 핵심 위험과 대응
| 위험 | 대응 |
|---|---|
| LLM의 잘못된 핀 번호 | 라이브러리 핀맵 및 근거 검증, 임의 핀맵 금지 |
| EDA 자동 배치 가독성 | 검증된 기능 블록 템플릿 우선 |
| KiCad 버전 차이 | 지원 버전 고정, CI에서 Golden 파일 검증 |
| 양방향 편집 데이터 충돌 | 초기에는 외부 수정 읽기 전용, 이후 명시적 import/diff |
| 설계 변경으로 기존 Net 손상 | 안정 ID, patch precondition, Netlist diff |
| 민감 설계 정보 API 유출 | 기본 로컬 저장, 명시적 전송 승인, 로그 마스킹 |
| AI 출력 스키마 불일치 | Pydantic 검증, 제한된 재시도, 실패 원인 표시 |

## 14. 비기능 요구사항
- Windows 11 우선, 오프라인 프로젝트 조회/EDA 작업 지원(로컬 KiCad 설치 필요).
- API 키는 OS 보안 저장소 활용; 프로젝트 JSON/로그에 평문 저장 금지.
- LLM API 호출 전 전송 데이터 범위 표시.
- 생성 작업 취소, 진행 상태, 실패 재시도 제공.
- 프로젝트 파일 원자적 저장 및 revision 백업.
- 앱 충돌 시 마지막 승인 버전 복구 가능.
- 테스트는 Python pytest + TypeScript Vitest/Playwright 검토.

## 15. Claude Code 개발 규칙
1. 첫 단계에서는 구현 전에 저장소 구조와 ADR-001(EDA 생성 전략)을 작성.
2. 한 번에 전체 앱 구현 금지. Phase 0의 단일 회로 PoC부터 구현.
3. 모든 핵심 데이터 구조에 Pydantic schema와 테스트 작성.
4. AI 응답을 직접 KiCad 파일로 쓰지 말고 IR 검증 후 변환.
5. KiCad CLI 실행은 subprocess 인자 배열로 수행; 쉘 문자열 연결 금지.
6. 검증되지 않은 부품 핀맵·정격은 `unverified`로 기록.
7. 생성 파일의 Netlist와 IR의 연결 그래프를 비교.
8. 작업 종료마다 변경 파일, 테스트 결과, 미해결 위험, 다음 단계 보고.

## 16. Claude Code 첫 실행 프롬프트
```text
당신은 Electron/React/TypeScript, Python/FastAPI, KiCad 자동화에 경험이 있는 수석 소프트웨어 엔지니어입니다.
첨부한 AI Circuit Studio 기획서를 제품 요구사항의 기준으로 사용하세요.
지금은 Phase 0만 진행합니다. 전체 제품을 한 번에 구현하지 마세요.

1. 현재 OS/개발환경/설치된 KiCad 버전을 탐지하고 결과를 보고하세요.
2. 기술 선택 ADR-001을 작성하세요: SKiDL/schematizer 방식과 직접 KiCad 파일 생성 방식의 장단점 및 실험 계획.
3. Pydantic Circuit IR 최소 모델(Component, Pin, Net, Project)을 정의하세요.
4. LED + 직렬저항 + 2핀 커넥터 회로의 수동 입력 fixture를 만드세요.
5. 실제 KiCad에서 열 수 있는 .kicad_sch 생성 PoC를 구현하세요.
6. KiCad CLI를 통해 ERC/Netlist/SVG 생성이 가능하면 자동 테스트하세요.
7. IR과 Netlist의 연결 구조를 비교하는 테스트를 작성하세요.
8. README에 실행 명령과 실패/제약 사항을 기록하세요.

주의: ERC 성공을 전기적 설계 검증 완료로 표현하지 마세요. 존재하지 않는 API나 심볼을 추측해서 사용하지 마세요. 도구가 설치되지 않았다면 실행한 척하지 말고 설치 방법과 검증 대기 상태를 보고하세요. 각 단계마다 실제 테스트 결과를 제시하세요.
```

## 17. MVP 인수 기준
- [ ] 프로젝트 생성·저장·재열기
- [ ] 요구사항 구조화 스키마 검증
- [ ] AI 설계안 승인/거부
- [ ] JSON/Markdown 내보내기
- [ ] LED 회로 .kicad_sch 생성 및 KiCad 열기
- [ ] 추출 Netlist와 IR 연결 구조 일치
- [ ] ERC 결과 및 경고 표시
- [ ] 승인된 변경의 revision 기록/복원
- [ ] 오류·미검증 항목 명시

## 18. 결정 대기 사항
- 지원 KiCad 메이저 버전
- Windows 단일 플랫폼 우선 여부
- LLM 제공자 및 API 키 입력 방식
- KiCad 생성 어댑터 PoC 결과에 따른 최종 채택
- 템플릿의 검증/승인 책임과 출처 관리 정책
