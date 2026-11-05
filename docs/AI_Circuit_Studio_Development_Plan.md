# AI Circuit Studio — 앱 개발 기획서 v1.9

- 작성일: 2026-11-04
- 목적: Claude Code에서 개발 착수 가능한 PRD + 기술 설계 + 구현 순서
- 대상: Windows 우선 로컬 데스크톱 앱
- 상태: v1.9. Phase 0~4 완료. 실제 부품 선정과 회로 검증은 별도 승인 필요.
- v1.1 개정 취지: AI EDA Agent의 제한된 도구 실행, 검증-수정 반복, 확장 가능한 PCB/FW 데이터 구조 추가.
- v1.2 개정 취지: v1.1 검토 결과 반영. 승인 경로 단일화, 상태 3축 분리, Agent 상태 머신 보완, 승인 범위 정의, 격리 작업공간 정의, 단계 순서 정리, Phase 0 결과 반영.
- v1.3 개정 취지: 사용자 요청 반영. 프로젝트→보드→페이지 구조, PDF 회로도 카피(Markdown 가져오기 + AI 변환 옵션), 보유 자재 우선 사용, AI 공급자 결정(Gemini 기본, 옵션), 제품 비전(하나의 입력 창에서 회로도·아트웍·펌웨어).
- v1.4 개정 취지: 사용자 요청 반영. 제조사가 확실한 부품의 데이터시트를 자동으로 받아 회로 분석(핀맵·정격·외부 부품)에 쓰고, 이후 회로 작성의 근거로 쓴다.
- v1.5 개정 취지: Phase 2 완료. 사용자 목표 "요구사항 적기 → AI가 회로도 자동 작성·검토"를 제안(proposal) 승인 경로 위에 구현. AI 설계 회로가 KiCad 검사를 통과하도록 생성기 항목 일부(no-connect, PWR_FLAG, 파생 심볼)를 Phase 3에서 앞당김.
- v1.6 개정 취지: 사용자 요청 반영. AI가 쓰는 글은 한국어, 검토 의견을 반영한 수정안, 읽기 쉬운 회로도(연결 기반 블록 배치, 전원 심볼, 부품 사이 직접 배선, 커넥터 좌우 반전), AI 공급자에 Claude 추가(API 키 또는 Anthropic CLI 로그인).
- v1.7 개정 취지: Phase 3(다중 유닛·겹친 핀, 검토된 템플릿, 엔지니어 검토 기록)과 Phase 4(제한된 AI EDA Agent) 완료. 템플릿 출처·책임 정책 결정(ADR-007), Agent 설계 결정(ADR-008).
- v1.8 개정 취지: 사용자 요청 반영. 회로도에 영문 주석(블록 설명·사양·Net 설명, IR 0.5.0 `notes`), 낱개 부품을 줄이는 배치(크리스탈 양단 콘덴서, 전원 입력 블록), MCU 없는 샘플 보드(ADR-009).
- v1.9 개정 취지: 사용자 요청 반영. 선택의 근거(요구 반영·요구 수정·AI 선정·템플릿·엔지니어)를 회로에 기록(IR 0.7.0 `decisions`), 계산 기반 기술 검토, 둘과 제안 이력을 묶은 근거 리포트, 계산 입력인 전원 조건(IR 0.8.0 Net 전압·부하 전류)(ADR-011).
- 변경 원칙: v1.0의 Phase 0 및 기존 MVP 범위를 보존하고 단계적 확장.

## 1. 제품 목표
사용자가 자연어로 회로 요구사항을 입력하면 AI가 요구사항을 구조화하고, 설계안과 부품 후보를 제시한다. 사용자가 승인한 설계를 정형 Circuit IR로 저장하고, Markdown 문서와 KiCad 회로도를 생성한다. 변경 제안은 diff를 검토·승인한 후 적용한다.

### 제품 비전 (v1.3)
궁극적으로는 **하나의 입력 창**에서 회로도, 아트웍(PCB), 펌웨어까지 다룬다. 사용자는 한 곳에 요구사항·변경 요청·질문을 입력하고, 앱이 보드 단위로 회로도(Circuit IR → KiCad), PCB(.kicad_pcb), 펌웨어(MCU 프로젝트)를 제안·생성·검증한다.
- 단위는 **보드**다. 보드 하나가 회로 하나, KiCad 프로젝트 하나이며, 나중에 그 보드의 PCB와 펌웨어 산출물이 같은 보드 아래에 붙는다(7.1절 아티팩트 모델의 pcb/gerber/firmware).
- 입력 창은 AI가 제안하고 사람이 승인하는 구조(1.1절)를 그대로 따른다. 회로도·아트웍·펌웨어 모두 같은 승인 게이트와 검증 증거를 거친다.
- 순서: 회로도(현재) → 변경 흐름·통합 입력 창(Phase 2) → 템플릿(Phase 3) → Agent(Phase 4) → 아트웍·펌웨어(Phase 5). 아트웍·펌웨어는 각 기능의 검증 방법이 정해지기 전에는 출시하지 않는다.

### 비목표 (초기)
- 완전 자동 PCB 레이아웃/라우팅
- 외부 임의 KiCad 파일의 완전한 양방향 편집
- ERC 통과만으로 전기적 안전/제작성 보장
- 데이터시트 미확인 부품의 자동 최종 승인

## 1.1 핵심 설계 결정
- Circuit IR(JSON)은 전기적 연결 정보의 기준 원본이며 Markdown은 요구사항, 설계 근거, 작업 기록을 표현한다.
- AI는 승인 전까지 **제안 또는 격리된 작업공간의 변경**만 수행한다.
- Agent는 임의 OS 명령이 아니라 allowlist로 제한된 EDA Tool Gateway를 통해 작업한다.
- KiCad 회로도 생성과 KiCad 내부 편집 자동화는 별개의 능력으로 취급한다. 각 기능은 설치된 KiCad 버전에서 검증한다.
- PCB/FW 연계는 데이터 모델만 확장 가능하게 만들고 MVP 구현 범위에서는 제외한다.
- **(v1.2) 승인 경로는 하나다.** 사람이 요청한 AI 제안이든 Agent 실행 결과든, 원본에 반영되는 모든 AI 변경은 `proposal`(IR patch + 검증 증거)로 표현되고 `/proposals/{id}/approve` 한 곳에서만 승인된다(FR-16).
- **(v1.2) 상태는 세 축으로 분리한다**(10.1절): 승인 상태, 자동 검사 결과, 엔지니어링 검토 상태. 하나의 값으로 섞어 표시하지 않는다.
- **(v1.2) 승인 화면의 diff는 코드가 계산한다.** AI가 작성한 변경 설명은 참고 정보일 뿐이며, 승인 근거는 IR에서 결정적으로 계산한 diff와 실행 증거다.
- **(v1.2) 사용자가 직접 편집한 IR**(Phase 1의 JSON 편집 등)은 사용자 자신의 행위이므로 proposal 없이 저장하되, 저장마다 revision을 남긴다.
- **(v1.3) 프로젝트 → 보드(A, B, …) → 페이지.** 보드 하나가 Circuit IR 하나, revision 이력 하나, KiCad 프로젝트 하나다. 페이지는 IR 안의 `sheets`로, 그리기 단위일 뿐 연결(Net)은 보드 전체 기준이다.
- **(v1.3) AI는 선택 기능이다.** 기본은 꺼져 있고, 켜면 Google Gemini API를 쓴다(공급자 교체 가능). 호출 전에 보낼 데이터를 보여주고 확인을 받으며, 키는 OS 보안 저장소에만 둔다.
- **(v1.6) Claude도 고를 수 있다.** 설정에서 Gemini/Claude를 고른다. Claude는 API 키 또는 Anthropic CLI(`ant auth login`) 로그인으로 쓴다. 로그인 토큰은 Anthropic CLI 폴더에 있고 앱은 읽지 않는다. claude.ai 구독(Pro/Max) 로그인은 다른 앱에서 쓸 수 없으므로 Console 계정(API 크레딧)으로 로그인한다(ADR-004).
- **(v1.5) AI 설계는 세 단계로 나눈다(ADR-006).** AI는 ① 부품 계획(역할·값·품번·KiCad 심볼 추정) ③ 연결(코드가 준 핀 번호만 사용)만 답하고, ② 부품을 실제 KiCad 심볼·핀으로 바꾸는 일과 모든 핀 참조 검사는 코드가 한다. 문제는 AI에게 돌려보내 최대 2회 고치게 한다. 결과는 항상 proposal이다.
- **(v1.4) 데이터시트는 요약을 거쳐서만 쓴다.** PDF는 원문으로 보관하고, 앱이 쓰는 것은 쪽 번호가 붙은 "데이터시트 요약 MD"(코파일럿·사람·AI가 작성)다. 판정(핀맵·정격·외부 부품)은 코드가 하고, 결과는 자동 검사 근거일 뿐 회로를 바꾸거나 엔지니어링 검토 상태를 바꾸지 않는다. 다운로드는 KiCad 심볼에 이미 들어 있는 제조사 링크만, 사용자 확인 후 HTTPS로 한다(ADR-005).
- **(v1.3) Phase 2a 전의 AI 결과 승인 방식:** PDF 변환 결과는 편집기에 불러와 사용자가 검토 후 저장하고, 자재 추천은 사용자가 고른 것만 적용한다. 이 두 흐름은 Phase 2a에서 proposal 승인 경로로 옮긴다.

## 2. 사용자 시나리오
1. 새 프로젝트 생성: STM32 CAN Controller.
2. 요구사항 입력: 24V DC 입력, 5V 2A, 3.3V 0.5A, STM32F103C8T6, SN65HVD230, 선택형 120Ω CAN 종단, SWD, 입력 보호.
3. AI가 모호한 요구사항(24V 입력 범위, 환경, 커넥터, 출력 부하 등)을 식별한다.
4. AI가 기능 블록·부품 후보·설계 근거·검증 대기 항목을 제시한다.
5. 사용자가 승인하면 Circuit IR에 반영하고 Markdown/JSON을 저장한다.
6. EDA Adapter가 KiCad 프로젝트(.kicad_pro + .kicad_sch)를 생성하고 KiCad CLI ERC/Netlist/SVG를 실행한다.
7. UI에서 생성 결과, 경고, 검증 상태를 표시한다.
8. 변경 요청 시 변경 제안(diff)을 보여주고 승인 후 버전으로 저장한다.

## 3. MVP 기능 요구사항
| ID | 기능 | 우선순위 | 완료 조건 | 단계 |
|---|---|---|---|---|
| FR-01 | 프로젝트 생성/열기/저장 | P0 | 로컬 프로젝트 재열기 시 상태 복원 | 1 |
| FR-02 | 요구사항 입력·편집 | P0 | 텍스트(1단계)와 필드형 사양(2단계) 저장 | 1, 2 |
| FR-03 | AI 요구사항 구조화 | P0 | 스키마 검증 가능한 결과 반환 | 2 ✅ |
| FR-04 | 설계안 제안 및 승인 | P0 | 승인 전 원본 변경 없음 | 2 ✅ |
| FR-05 | Circuit IR 관리 | P0 | component/pin/net ID 안정적 유지 | 1 |
| FR-06 | Markdown/JSON 내보내기 | P0 | 동일 IR에서 결정적 생성 | 1 |
| FR-07 | KiCad 회로도 생성 | P0 | 지원 회로에서 KiCad 프로젝트가 열림 | 0, 1 |
| FR-08 | ERC·Netlist·SVG 결과 표시 | P0 | 실행 결과와 오류를 UI에서 확인 | 1 |
| FR-09 | 변경 diff·버전 이력 | P1 | 승인/취소/롤백 가능 | 1(이력), 2(diff·승인) ✅ |
| FR-10 | BOM CSV | P1 | ref/value/MPN/footprint 포함 | 1 |
| FR-11 | 검증 상태 추적 | P0 | 상태 3축(10.1절) 구분 표시. (v1.7) 엔지니어링 검토 축은 검토 기록에서 계산 | 1, 3 ✅ |
| FR-12 | 데이터시트 수집·첨부 | P1 | (v1.4) KiCad 심볼 링크로 받기 또는 직접 올리기. PDF 본문에서 품번 확인, 출처 URL·해시 기록. 워크스페이스 공통 라이브러리 | 1.6 ✅ |
| FR-13 | Agent 작업 계획/도구 호출 기록 | P1 | 입력·결과·시각·revision 연계 | 4 ✅ |
| FR-14 | 제한된 EDA 도구 실행 | P1 | 허용된 명령만 실행, 작업별 타임아웃 | 4 ✅ |
| FR-15 | 자동 재검증 루프 | P1 | 제한 횟수 내 수정 제안·재검증·보고 | 4 ✅ |
| FR-16 | 승인 게이트 | P0 | 승인 없는 원본 덮어쓰기·제조 산출물 확정 금지. 승인 경로는 하나 | 2 ✅ |
| FR-17 | PCB/FW 확장용 아티팩트 참조 | P2 | 회로 revision과 결과 파일 연결 | 1(스키마), 5 |
| FR-18 | 여러 보드·여러 페이지 | P0 | 프로젝트에 보드 여러 개, 보드마다 페이지 여러 장. 페이지를 나눠도 Netlist 동일 | 1.5 ✅ |
| FR-19 | PDF 회로도 카피 1단계: 회로도 MD 가져오기 | P0 | 코파일럿 등이 만든 "회로도 MD v0.1"을 IR로 변환, 라이브러리 핀 대조, 불확실 항목은 검토 필요 | 1.5 ✅ |
| FR-20 | PDF 회로도 카피 2단계: AI 변환(옵션) | P1 | Gemini로 PDF → 회로도 MD → 1단계와 같은 가져오기. 전송 확인·실행 기록 | 1.5 ✅ |
| FR-21 | 보유 자재 우선 사용 | P0 | 자재 목록(CSV/XLSX) 대조: 보유/대체 가능/미보유, 재고 확인, 품번·풋프린트 적용(revision) | 1.5 ✅ |
| FR-22 | AI 대체 자재 추천(옵션) | P1 | 보유 자재 목록 안에서만 추천, 사용자가 고른 것만 적용 | 1.5 ✅ |
| FR-23 | 통합 입력 창 | P1 | 하나의 입력 창에서 요구사항·변경 요청 → proposal. 이후 아트웍·펌웨어로 확장 | 2 ✅(회로도: 요구사항 탭 → AI 설계 → 제안), 5(아트웍·펌웨어) |
| FR-24 | 데이터시트 활용 (v1.4) | P1 | 데이터시트 요약 MD(코파일럿 프롬프트 제공, AI 요약 옵션)로 핀맵 대조, 전원 전압 ↔ 정격 점검, 외부 부품 체크리스트. 응용 회로 메모는 회로 작성 근거로 보관 | 1.6 ✅ (작성 활용은 2b) |
| FR-25 | 제조사 정보 (v1.4) | P1 | IR 부품에 `manufacturer`. 회로도 MD·보유 자재 적용·BOM에 반영 | 1.6 ✅ |

## 4. 권장 기술 스택
- Electron + React + TypeScript + Vite: 데스크톱 셸 및 UI.
- Python 3.12+ + FastAPI + Pydantic v2: 회로 데이터 모델, 검증, 변환 백엔드.
- LLM API: **Google Gemini 기본**(`google-genai` SDK), **(v1.6) Anthropic Claude 선택**(`anthropic` SDK, 기본 모델 `claude-opus-5-5`), 둘 다 선택 기능(ADR-004). 앱의 LLM 공급자는 교체 가능한 인터페이스로 구현한다. **(v1.2)** 공급자마다 tool calling 형식이 다르므로 Gateway 도구는 자체 Pydantic 스키마로 정의하고 공급자별 어댑터에서 변환한다.
- KiCad **10.0.x** (ADR-002, 검증 기준 10.0.8): 공식 파일 형식, CLI ERC, Netlist, SVG. CLI 지원 옵션은 설치 버전에서 확인.
- SQLite: 프로젝트 인덱스, 작업 이력, 승인 이력. 프로젝트 원본은 JSON 및 Markdown 파일.
- `keyring`: API 키를 OS 보안 저장소(Windows 자격 증명 관리자)에 보관. `openpyxl`: 자재 목록 XLSX 읽기. `pypdf`: 데이터시트 PDF의 본문 글자 읽기(품번 확인용, 순수 Python).
- React Flow: 기능 블록도에만 사용. 전자회로 편집기 자체의 대체재로 가정하지 않음.
- 회로도 생성: 직접 `.kicad_sch` 생성기(ADR-001). SKiDL/schematizer는 Phase 0 비교 실험 결과에 따라 채택하지 않음.

## 5. 시스템 구성
```text
Electron / React UI
  ├─ Project Explorer
  ├─ Requirements Editor
  ├─ AI Proposal + Diff Review
  ├─ Block Diagram
  ├─ Schematic Preview (SVG)
  └─ Validation / BOM / History
          │ local HTTP (127.0.0.1, 실행마다 바뀌는 토큰)
Python Application API
  ├─ Project Service
  ├─ AI Orchestrator (structured proposals + tool plans)
  ├─ Agent Planner / Executor / Observer
  ├─ EDA Tool Gateway (allowlist, workspace path limits, timeout)
  ├─ Circuit IR Service
  ├─ Rules Validator
  ├─ KiCad Adapter
  ├─ Exporter (MD/JSON/BOM)
  ├─ Revision Service (원본 반영의 유일한 경로)
  └─ Agent Audit Service
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
- **(v1.2)** 생성 전에 부품이 선언한 핀은 라이브러리 심볼의 핀 번호·이름과 정확히 일치해야 한다(Phase 0 구현). 불일치는 오류이며 파일을 만들지 않는다.

### IR 예시 (설명용, 제작용 아님)
```json
{
  "schema_version": "0.1.0",
  "project_id": "stm32-can-demo",
  "components": [
    {"id":"res-001", "reference":"R1", "value":"330", "symbol_id":"Device:R",
     "pins":[{"number":"1"}, {"number":"2"}]},
    {"id":"led-001", "reference":"D1", "value":"LED", "symbol_id":"Device:LED",
     "pins":[{"number":"1", "name":"K"}, {"number":"2", "name":"A"}]}
  ],
  "nets": [
    {"id":"net-001", "name":"LED_A", "nodes":[{"component_id":"res-001","pin":"2"},{"component_id":"led-001","pin":"2"}]}
  ],
  "validation": {"status":"unverified"}
}
```
위 예시는 완전한 회로가 아니다. KiCad 10 `Device:LED`의 핀 번호는 1=K(캐소드), 2=A(애노드)이며(Phase 0에서 라이브러리로 확인), 연결은 이름(`A`/`K`)이 아니라 번호로 저장한다. 정식 스키마는 `docs/schema/circuit-ir.schema.json`.

## 7. 데이터 및 프로젝트 구조
```text
AI-Circuit-Studio/
  desktop/                 # Electron + React
  backend/                 # FastAPI
  circuit_core/            # Pydantic models / rules / exporters
  kicad_adapter/           # export, ERC, netlist, SVG
  ai_engine/               # prompts, structured outputs, proposals (Phase 2)
  tests/                   # unit / golden-file / integration
  templates/               # reviewed circuit blocks (Phase 3)
  examples/                # KiCad에서 바로 여는 예제 프로젝트
  docs/                    # ADR, schema, specs
<workspace>/
  index.sqlite             # 목록·작업 이력·설정(폴더에서 다시 만들 수 있는 캐시)
  inventory/               # 보유 자재 목록(워크스페이스 공통): inventory.json, 원본 파일
  datasheets/              # (v1.4) 데이터시트 라이브러리(워크스페이스 공통): index.json, ds-<해시>.pdf,
                           #        .summary.md(요약 원문), .facts.json(앱이 읽은 결과), ai_runs/
  projects/<project-id>/
    project.json           # 메타데이터, 보드 순서 (파일 버전 2)
    requirements.md
    design_notes.md
    boards/<board-id>/     # (v1.3) 보드 = 회로 하나 = KiCad 프로젝트 하나
      board.json           # 이름, 현재 revision
      circuit.json         # 현재 Circuit IR (페이지는 IR의 sheets)
      revisions/           # <번호>.circuit.json 스냅샷 + 메타
      imports/             # 가져온 Markdown·PDF와 AI가 만든 Markdown 사본
      ai_runs/             # AI 호출 기록(시각, 모델, 입력 해시·크기, 토큰, 결과)
      proposals/           # (Phase 2) 승인 대기 patch와 검증 증거
      agent_runs/<run_id>/ # (Phase 4) workspace/, log.jsonl
      outputs/             # 마지막으로 생성한 revision의 산출물
        manifest.json      # 아티팩트 목록(7.1절)과 페이지 목록
        <project>-<board>.kicad_pro / .kicad_sch / -p<n>-<page>.kicad_sch
        <project>-<board>.svg / -<페이지 이름>.svg / .net, erc.json
        circuit.md, circuit.json, bom.csv
```
- **(v1.3)** 1.0 구조(프로젝트 폴더에 circuit.json)는 처음 열 때 `boards/a/`(보드 A)로 자동 이관한다.
- **(v1.3)** 여러 페이지일 때 1페이지가 루트 회로도이고 나머지 페이지는 계층 시트로 연결된다. 페이지를 오가는 Net은 global label, 한 페이지 안의 Net은 local label을 쓴다.
- **(v1.6)** 자동 배치는 IC를 중심으로 연결된 부품을 묶어 그린다(직렬 부품은 선을 이어서, GND·풀업 부품은 선에 걸쳐서, 전원 사이 부품은 전원 핀 옆에 세워서). 전원·GND는 KiCad 전원 심볼로, 가까운 신호 핀은 wire로 잇고 나머지는 label로 둔다. 커넥터는 IC를 향하도록 회전 대신 좌우 반전(mirror)해 1번 핀이 위에 오게 한다. 연결 정확성은 KiCad Netlist = IR 검사로 보장한다.
- **(v1.2)** `.kicad_sch`는 반드시 같은 이름의 `.kicad_pro`와 함께 만든다. 단독으로 열면 KiCad가 프로젝트 없이 동작해 무관한 라이브러리 경고가 생긴다(Phase 0 사용자 확인).
- **(v1.2)** `outputs/`는 "마지막으로 생성한 revision"의 산출물만 둔다. `manifest.json`의 `source_revision`이 현재 revision과 다르면 UI는 "오래됨(stale)"으로 표시한다. revision별 산출물 보관은 Phase 2에서 필요하면 추가한다.
- **(v1.2)** 프로젝트 원본은 사용자 워크스페이스(기본 `문서/AI Circuit Studio`, 설정 가능)에 두며 앱 설치 폴더와 분리한다.

## 7.1 확장 아티팩트 모델
- `artifact_id`, `artifact_type`(schematic, kicad_project, netlist, erc_report, svg, markdown, json, bom, pcb, gerber, firmware, simulation), `source_revision`, `tool_version`, `checksum`(SHA-256), `validation_status`, `created_at`을 기록한다.
- PCB/펌웨어 생성 기능은 MVP에 포함하지 않으며 추후 연결을 위한 참조 스키마만 마련한다.
- 작업 기록에는 실행 시각(ISO 8601 + 시간대), 입력 요약, 도구 버전, 결과, 승인자, 오류를 남긴다.
- **(v1.2) 외부 수정 감지:** 생성기는 결정적이므로 checksum이 의미를 가진다. 사용자가 KiCad에서 회로도를 저장하면 checksum이 달라지고, UI는 "외부에서 수정됨"으로 표시한다. 외부 수정은 읽기 전용으로 취급하며 재생성 시 덮어쓰기 전에 경고한다(13장 양방향 편집 위험 대응).
- **(v1.2) 작업 기록 저장:** 각 실행 폴더의 JSONL 파일이 원본이고 SQLite는 검색용 인덱스다. API 키·토큰 등 민감 정보는 기록 전에 마스킹한다.

## 8. API 계약 초안

모든 경로는 `/api` 아래에 있다(예: `/api/projects`). 구현된 계약은 실행 중인 백엔드의 `/api/docs`(OpenAPI)에서 확인한다.

> **(v1.3)** 회로에 관한 경로는 모두 보드 아래로 옮겼다: `/projects/{id}/boards/{board}/circuit` 등. 아래 목록의 `/projects/{id}/circuit`, `/revisions`, `/exports`, `/outputs`, `/documents`, `/jobs`는 `/projects/{id}/boards/{board}/…`로 읽는다.
> 추가된 경로: `GET|POST /projects/{id}/boards`, `GET|PATCH|DELETE /projects/{id}/boards/{board}`, `POST …/boards/{board}/imports/markdown`, `POST …/imports/pdf`(AI, job), `GET …/inventory-match`, `POST …/inventory-apply`, `GET …/inventory-suggest/preview`, `POST …/inventory-suggest`(AI, job), `GET|DELETE /inventory`, `POST /inventory/import`, `GET|PUT /ai/settings`, `PUT|DELETE /ai/key`, `GET /ai/models`, `GET /examples`.
> **(v1.4)** 데이터시트: `GET /datasheets`, `POST /datasheets/upload`, `POST /datasheets/{ds}/link`, `DELETE /datasheets/{ds}`, `GET /datasheets/{ds}/file`, `GET|POST /datasheets/{ds}/summary`, `PUT /datasheets/{ds}/reviewed`, `GET …/boards/{board}/datasheets`(부품별 상태·대조 결과), `POST …/boards/{board}/datasheets/fetch`(job, 보드 심볼의 링크만), `POST …/boards/{board}/datasheets/{ds}/summarize`(AI, job), `GET /prompts/{name}`(코파일럿 프롬프트).

- `POST /projects` : 프로젝트 생성
- `GET /projects` : 프로젝트 목록
- `GET /projects/{id}` : 프로젝트 메타데이터
- `PATCH /projects/{id}` : 이름·설명 변경
- `DELETE /projects/{id}` : 인덱스에서 제거하고 폴더를 워크스페이스 휴지통으로 이동(복구 가능)
- `GET|PUT /projects/{id}/requirements` : 요구사항 Markdown
- `POST /projects/{id}/requirements/parse` : 요구사항 → 구조화 스펙 (Phase 2)
- `GET|PUT /projects/{id}/circuit` : Circuit IR 조회/사용자 직접 저장(검증 후, revision 생성)
- `POST /projects/{id}/circuit/validate` : 저장 없이 L0/L1(+라이브러리) 검사
- `GET /projects/{id}/revisions` : 변경 이력
- `POST /projects/{id}/revisions/{revision_id}/restore` : 확인 후 복원(새 revision으로 기록)
- `POST /projects/{id}/exports` : 현재 revision의 모든 산출물 생성(KiCad 프로젝트 + ERC/Netlist/SVG + Markdown/JSON/BOM). job으로 실행. 외부 수정이 있으면 `force` 없이는 409
- `GET /projects/{id}/documents/markdown`, `GET /projects/{id}/documents/bom` : 현재 IR에서 계산한 문서 미리보기(파일 생성 없음)
- `GET /projects/{id}/outputs` : manifest와 상태(stale, 외부 수정, 누락)
- `GET /projects/{id}/outputs/files/{name}` : manifest에 정의된 산출물 파일
- `GET /projects/{id}/jobs` : 작업 이력
- `POST /projects/{id}/open`, `GET|PUT /settings` : 마지막 작업 상태 저장·복원
- `GET /system` : 앱 버전, 워크스페이스, KiCad·심볼 라이브러리 상태
- `GET /jobs/{job_id}` : 오래 걸리는 작업 상태
- `POST /projects/{id}/proposals` : AI 설계 제안 (적용하지 않음, Phase 2)
- `POST /projects/{id}/proposals/{proposal_id}/approve|reject` : **유일한 승인 경로** (Phase 2)
> **(v1.5)** 구현: `GET|POST …/boards/{board}/proposals`, `GET …/proposals/{id}`, `POST …/proposals/{id}/check|approve|reject`, `GET …/proposals/{id}/files/{name}`. AI: `GET|PUT /projects/{id}/requirements/spec`, `POST /projects/{id}/requirements/structure`(job), `GET …/boards/{board}/ai/design/preview`, `POST …/ai/design`(job), `POST …/ai/review`(job), `GET …/ai/review`, `POST …/ai/revise`(job).
> **(v1.7)** 구현: 템플릿 `GET /templates`, `GET /templates/{id}`, `POST …/boards/{board}/templates/{id}`(제안 생성). 검토 `GET|POST …/boards/{board}/reviews`, `GET …/reviews/checklist`. Agent `GET|POST …/boards/{board}/agent-runs`, `GET …/agent-runs/{id}`(기록 포함), `POST …/agent-runs/{id}/approve-scope`, `POST …/agent-runs/{id}/cancel`, `GET /agent/tools`.
- `POST /projects/{id}/agent-runs` : 격리된 Agent 실행 생성 (기본 dry-run, Phase 4). 결과는 proposal로 제출된다.
- `GET /projects/{id}/agent-runs/{run_id}` : 계획, 도구 실행, 로그, 검증 결과
- `POST /projects/{id}/agent-runs/{run_id}/cancel` : 실행 취소

오류 응답은 error_code, message, affected_component_ids, recovery_hint 필드를 포함. 오래 걸리는 EDA 작업은 job_id와 상태 조회 API를 사용.

> v1.1의 `POST /agent-runs/{run_id}/approve`는 삭제했다. Agent 결과의 승인은 그 run이 제출한 proposal의 승인으로 한다.

## 9. 설계 변경 처리
1. 변경은 `add_component`, `remove_component`, `update_property`, `connect`, `disconnect`, `move`(배치) 등 허용된 연산의 patch로 표현한다. **(v1.5)** 구현 이름: `set_property`(=update_property), `add_component`, `remove_component`, `connect`, `disconnect`, `rename_net`, `set_no_connect`, `move`, `replace_circuit`(첫 설계·가져오기). 각 연산은 precondition(현재 값, 기호)을 갖고 하나라도 맞지 않으면 patch 전체를 거부한다.
2. 서버가 patch의 base revision과 현재 revision, precondition을 확인한다.
3. schema/참조/핀/Net 규칙을 검증한다.
4. UI에서 영향 범위와 before/after diff를 제공한다. diff는 서버 코드가 IR에서 계산한다.
5. 승인 시 Revision Service가 원자적으로 새 revision을 저장한다.
6. EDA 재생성, Netlist 비교, ERC 실행.
7. 실패하면 기존 revision 보존, 오류 보고.

### 9.0 승인 범위와 재승인 (v1.2)
- 승인 범위(scope)는 "허용 연산 + 대상 ID" 목록으로 기록한다. 예: `update_property(res-001, value)`.
- 범위 밖의 연산이 하나라도 포함되면 재승인을 받는다.
- **설계 의미를 바꾸는 수정**: 연결 변경(`connect`, `disconnect`), 부품 추가·삭제, 전기적 속성(value, symbol_id, footprint_id, MPN) 변경. 이들은 항상 승인 대상이다.
- **의미를 바꾸지 않는 수정**: 배치(`move`), 표시용 메타데이터. 승인된 범위 안에서는 재승인이 필요 없다.

## 9.1 AI EDA Agent 실행 상태 머신 (v1.2 개정)
```text
PROPOSED_PLAN → SCOPE_APPROVED → SANDBOX_EXECUTION → VALIDATION ─┬─→ REVIEW_REQUIRED → COMMITTED
                                       ↑                          │          │
                                       └── (실패, 최대 2회) ───────┘          ├─→ REJECTED
어느 단계에서든: → CANCELLED (사용자 취소) / FAILED (상한 초과·오류) / STALE (base revision 변경)
```
- `DRAFT_REQUIREMENTS`는 Agent 실행이 아니라 프로젝트 단계이므로 상태 머신에서 뺐다.
- **(v1.7)** 구현에는 계획을 만드는 동안의 일시 상태 `PLANNING`이 앞에 있다. 도구 호출은 단계마다 JSON 행동 하나(공급자 중립 구조화 출력)이고, 범위 밖 변경이 필요하면 `request_scope_change`로 PROPOSED_PLAN에 돌아가 재승인을 받는다(ADR-008).
- 재시도: VALIDATION 실패 시 SANDBOX_EXECUTION으로 돌아가며 동일 실행에서 최대 2회. 재시도 중 범위 밖 연산이 필요하면 SCOPE_APPROVED 이전으로 돌아가 재승인을 받는다.
- `REVIEW_REQUIRED`에서 run은 proposal을 제출하고, 사용자의 approve/reject가 `COMMITTED`/`REJECTED`를 결정한다.
- `STALE`: 실행 중 원본 revision이 바뀌면 제출 불가. 새 base에서 다시 실행한다.
- 커밋 이후 되돌리기는 상태 전이가 아니라 revision 복원(새 revision 생성)으로 처리한다. v1.1의 `ROLLED_BACK`은 이 의미로 대체한다.
- Tool Gateway 허용 도구: `read_project`, `read_revision`, `read_symbol_library`, `validate_ir`, `propose_ir_patch`, `diff_ir`, `generate_schematic`, `run_erc`, `export_netlist`, `export_svg`, `compare_netlists`.
  - `read_symbol_library`는 설정된 KiCad 라이브러리 폴더로 제한한다.
  - 파일을 만드는 도구는 해당 run의 `workspace/` 안에만 쓴다. 모든 경로 인자는 Gateway가 검사한다.
  - 각 도구의 입력은 Pydantic 스키마로 검증하고, 실행마다 타임아웃을 둔다.
- 원본 삭제, 외부 프로그램 설치, 네트워크 업로드, Gerber 릴리스는 별도 명시적 승인 없이는 불가하다.
- **격리 작업공간의 정의:** OS 수준 보안 샌드박스가 아니라 구조적 격리다. Agent에게는 셸이 없고, Gateway 도구는 앱의 Python 함수뿐이며, 쓰기 경로는 run의 `workspace/`로 제한되고, 원본 반영은 Revision Service만 한다.
- **비용 상한:** 실행당 LLM 호출 수, 토큰, 경과 시간, 추정 비용의 상한을 설정값으로 두고, 넘으면 `FAILED`(사유: limit_exceeded)로 끝낸다. **(v1.7) 기본값:** LLM 호출 12회, 토큰 300,000, 900초, 도구 호출 30회, 재시도 2회. 공급자마다 단가가 달라 금액 상한 대신 토큰 상한을 둔다(ADR-008).
- **프롬프트 인젝션:** 로그·데이터시트의 명령형 텍스트는 프롬프트로 막을 수 있다고 가정하지 않는다. 도구 권한 제한, 비신뢰 입력 표시, 코드가 계산한 diff와 승인 게이트로 구조적으로 막는다.

## 10. 검증 전략
- L0 Schema: 타입/필수 필드/참조 ID/중복 검증.
- L1 Topology: 핀 존재 여부, 미연결, Net 충돌, 연결 그래프 정합성. 라이브러리 핀맵 대조 포함.
- L2 EDA: KiCad CLI ERC, netlist export, schematic export, Netlist ↔ IR 비교.
- L3 Engineering: 데이터시트 핀맵, 전압·전류 정격, 보호회로, 발열, 전원 시퀀스, EMC/ESD 등 설계 검토. **(v1.4)** 이 중 핀맵, 전원 전압 ↔ 정격, 필수·권장 외부 부품은 데이터시트 요약으로 자동 사전 점검한다. 사전 점검은 사람의 검토를 대신하지 않는다. **(v1.9)** 데이터시트가 없어도 되는 계산 항목(콘덴서 정격·극성, LED 방향·전류, 저항 전력, 레귤레이터 드롭아웃·발열, 디커플링)은 코드가 계산해 「검토」 탭에 확인할 목록으로 보여 준다. 상태 3축은 바꾸지 않는다(ADR-011).
- L4 Physical: 시뮬레이션·시제품 시험. MVP 범위 밖.

ERC 통과는 설계 적합성의 충분조건이 아님. AI가 검증하지 않은 사항을 검증 완료로 표시하지 않음.

### 10.1 상태 3축 (v1.2)
| 축 | 단위 | 값 | UI 표시 |
|---|---|---|---|
| 승인 상태 | revision / proposal | `proposed` → `approved` / `rejected` → `committed` | 제안됨 / 승인됨 / 거부됨 |
| 자동 검사 결과 | revision의 산출물 | `not_checked` / `passed` / `failed` + 증거(KiCad 버전, 리포트 checksum, 실행 시각) | Not checked / Automated checks passed / Automated checks failed |
| 엔지니어링 검토 | 부품·요구사항·설계 | `unverified` / `needs_review` / `verified` + 검토자·근거 | 미확인 / 검토 필요 / Engineer reviewed |

- 세 축은 서로 독립이다. "자동 검사 통과"가 "검토됨"을 의미하지 않는다.
- `verified`는 사람만 설정할 수 있다. AI와 자동 검사는 `verified`를 설정하지 않는다.

## 11. EDA 생성 전략 비교 PoC
A. SKiDL + schematizer: 빠른 초기 평가. 지원 버전, 라이브러리 호환, 배치 품질 확인.
B. 직접 KiCad schematic generator: 제어력 높으나 심볼 인스턴스, 라이브러리 참조, UUID, 좌표, 배선, 버전 호환 구현 비용 큼.

선택 기준: 파일 열림, ERC, Netlist 정확성, 회로 배치 가독성, 버전 안정성, 변경 시 결정성, 개발 비용. 결과는 ADR-001.

## 11.1 Agent 접근법 검증 PoC
- 파일 기반 방식: Circuit IR patch → KiCad generator → ERC/Netlist.
- 도구 기반 방식: AI가 허용된 Gateway 도구를 순차 호출해 같은 작업 수행.
- 평가 지표: 전기적 Netlist 일치율(기대 IR 대비), 무승인 변경 0건, 재현 가능성, 실패 복구, 작업 시간, 로그 완전성.
- 특정 모델 또는 기사에 등장한 자동화 성능을 현재 앱에서 재현할 수 있다고 가정하지 않는다.

## 12. 단계별 구현 계획 및 게이트
### Phase 0 — 환경/PoC (완료: 2026-11-04)
- Electron/React, Python, KiCad 버전 확인 및 설치.
- 수동 Circuit IR로 저항 + LED + 커넥터 회로 생성.
- KiCad에서 열기, CLI ERC, Netlist, SVG 확인.
- 생성 엔진 A/B 평가, ADR 기록.
- Gate: Golden test 회로가 열리고 의도한 Netlist와 일치.
- 결과: `docs/phase0-report.md`, ADR-001, ADR-002.

### Phase 1 — Core MVP (완료: 2026-11-04)
- 프로젝트 CRUD(생성/목록/열기/이름 변경/휴지통 삭제), SQLite 인덱스, 마지막 작업 상태 복원.
- 요구사항 텍스트 편집·저장(필드형 사양은 Phase 2에서 AI 구조화 스키마와 함께).
- Circuit IR 가져오기·편집·검증·저장, 저장마다 revision, revision 복원.
- Markdown/JSON/BOM exporter(결정적).
- EDA Adapter job(생성 + ERC + Netlist 비교 + SVG), validation UI, 생성 파일 경로 관리(manifest, stale·외부 수정 감지).
- Gate: 앱 재시작 후 프로젝트 복원, 동일 입력에서 동일 연결 구조 출력.
- 결과: `docs/phase1-report.md`, ADR-003.

### Phase 1.5 — 보드·페이지, 가져오기, 보유 자재 (완료: 2026-11-04, v1.3 추가)
- 프로젝트→보드→페이지 구조, 여러 페이지 KiCad 생성(계층 시트, global label), 자동 배치와 용지 자동 선택.
- PDF 회로도 카피 1단계(회로도 MD 가져오기), 2단계(Gemini 변환, 옵션).
- 보유 자재 목록과 자동 대조·적용, AI 대체 추천(옵션).
- Gate: 페이지를 나눠도 KiCad Netlist = IR, 페이지별 KiCad 재저장 무손실, 가져온 회로의 핀은 라이브러리로만 번호화, AI 결과는 사용자 확인 없이 저장되지 않음.
- 결과: `docs/phase1_5-report.md`, ADR-004.

### Phase 1.6 — 데이터시트 수집·활용 (완료: 2026-11-04, v1.4 추가)
- IR 부품에 제조사(`manufacturer`). 회로도 MD 2장 「제조사」 열(선택), 보유 자재 적용, BOM에 반영.
- 워크스페이스 데이터시트 라이브러리: KiCad 심볼 링크로 받기(확인 후, HTTPS, 50MB, PDF만) 또는 직접 올리기. 본문에서 품번·제품군 확인.
- 데이터시트 요약 MD v0.1 형식과 코파일럿 프롬프트, AI 요약(옵션, Gemini).
- 대조: 핀맵(패키지 자동 선택), 전원 전압(Net 이름) ↔ 절대 최대 정격·권장 조건, 외부 부품 체크리스트. 응용 회로 메모 보관.
- Gate: 대조는 회로를 바꾸지 않음, 보드 심볼에 없는 링크는 받지 않음, 일반 심볼(Device:R 등)에는 데이터시트를 연결하지 않음, AI 실행 기록에 API 키를 남기지 않음.
- 결과: `docs/phase1_6-report.md`, ADR-005, `docs/prompts/datasheet-summary.md`.

### Phase 2 — Change Workflow → AI Workflow (완료: 2026-11-04)
- **2a (LLM 없음, v1.2 추가):** patch 연산, diff, proposal, 승인/거부, revision, 격리 작업공간, 재생성·재검증, 작업 기록. 수직 슬라이스: LED 회로 저항값 330→1k patch → diff → 승인 → 생성 → ERC → Netlist 비교.
  - 통합 테스트: 승인 시 Netlist 부품값 변경 확인. 거부·실패 시 원본 파일 해시 불변.
- **2b:** LLM 공급자 어댑터, 요구사항 구조화(필드형 사양), 설계안 생성. LLM은 2a의 proposal을 만드는 역할만 한다. **(v1.4)** 설계안은 데이터시트 요약(응용 회로 메모, 외부 부품, 정격)을 쪽 번호와 함께 근거로 인용하고, 제안 후 같은 데이터시트 대조를 다시 통과해야 한다.
- Gate: 승인 전 데이터 불변, 거부 시 변경 없음, 수정 후 재검증.
- **(v1.5) 구현:** 요구사항 탭 → (AI 요구사항 정리) → AI 회로 설계(부품 계획 → KiCad 심볼·핀 확정 → 연결 → 코드 검사·최대 2회 수정) → proposal → 격리 검사(IR 규칙, KiCad ERC·Netlist, 데이터시트) → AI 검토 → 승인 → revision·재생성. AI 검토 의견으로 수정안 만들기. PDF 가져오기(AI)도 proposal로.
- 결과: `docs/phase2-report.md`, ADR-006.

### Phase 3 — Engineering Templates (완료: 2026-11-05)
- 생성기 확장: 전원 심볼·PWR_FLAG, 다중 유닛 심볼, no-connect 표시(MCU 회로의 전제). **(v1.5)** no-connect, PWR_FLAG 자동 추가(커넥터로 전원이 들어오는 Net), 파생 심볼(`extends`)은 Phase 2에서 완료. **(v1.6)** 전원 심볼, 부품 사이 직접 배선, 연결 기반 블록 배치, 커넥터 좌우 반전도 완료(ADR-001 갱신). 남은 것: 다중 유닛 심볼(OP앰프, 일부 MCU), 사람이 손본 배치를 다음 생성에도 유지하는 기능.
- 전원부, MCU 최소 회로, CAN 회로 템플릿 확장.
- 실제 데이터시트 기반 핀맵/정격 검토 프로세스. **(v1.4)** 자동 사전 점검(Phase 1.6) 위에 엔지니어 검토 기록(누가, 어느 데이터시트 개정, 어느 쪽)을 더한다.
- Gate: 각 템플릿에 근거와 검증 체크리스트 첨부.
- **(v1.7) 구현:** 다중 유닛 심볼(유닛별 배치), 겹친 숨은 핀(같은 Net 강제; KiCad 10 라이브러리에는 숨은 power_in 핀이 없음), 템플릿 4종(AMS1117 LDO, LM2596 벅, SN65HVD230 CAN, ATmega328P 최소 회로), 템플릿 넣기(제안), AI 설계의 템플릿 우선 사용과 부품 출처(origin) 표시, 엔지니어 검토 기록. 쪽 번호는 원문을 열 수 없어 검토 기록에서 채운다(ADR-007).
- 결과: `docs/phase3-report.md`, ADR-007.

### Phase 4 — Bounded AI EDA Agent (완료: 2026-11-05)
- 2a의 proposal·revision·격리 작업공간 위에 EDA Tool Gateway, 실행 계획, 상태 머신, 비용 상한, 작업 로그를 구현.
- AI가 기존 LED 회로의 저항값을 변경하도록 제안하고, 승인된 범위 내에서 재생성 및 ERC 수행.
- Gate: 원본 무단 변경 0건, 실패 시 복구, 변경 전후 Netlist/부품값 차이 보고.
- **(v1.7) 구현:** Tool Gateway(허용 도구 11개 + finish·request_scope_change, Pydantic 인자, 시간 제한, workspace 제한), 상태 머신, 범위 승인과 코드 강제, 코드 검증과 재시도 2회, 비용 상한, JSONL 기록(키 가림), 전후 보고(IR diff + KiCad Netlist 비교), 「Agent」 탭. LED R1 330→1k 시나리오를 KiCad 10.0.8로 확인.
- 결과: `docs/phase4-report.md`, ADR-008.

### Phase 5 — 아트웍·펌웨어와 통합 입력 창 확장 (장기, MVP 제외)
- 제품 비전(1장): 하나의 입력 창에서 회로도·아트웍(PCB)·펌웨어.
- KiCad 외부 수정 import/diff, PCB 설계 보조(배치·배선 제안, DRC), 제조 검토, Gerber, SPICE, 펌웨어 프로젝트 생성(핀맵·주변장치 설정을 Circuit IR에서 유도).
- 각 기능별 지원 범위와 실물 시험 결과를 별도로 검증한 뒤 출시 여부 결정.

기간은 단독 개발의 잠정 추정치이며 작업 시간과 도구 호환성에 따라 달라짐.

## 13. 핵심 위험과 대응
| 위험 | 대응 |
|---|---|
| LLM의 잘못된 핀 번호 | 라이브러리 핀맵 및 근거 검증, 임의 핀맵 금지, 불일치 시 생성 거부 |
| EDA 자동 배치 가독성 | 검증된 기능 블록 템플릿 우선 |
| KiCad 버전 차이 | 지원 버전 고정(10.0.x), CI에서 Golden 파일·round-trip 검증 |
| 양방향 편집 데이터 충돌 | 초기에는 외부 수정 읽기 전용(checksum 감지), 이후 명시적 import/diff |
| 설계 변경으로 기존 Net 손상 | 안정 ID, patch precondition, Netlist diff |
| 민감 설계 정보 API 유출 | 기본 로컬 저장, 명시적 전송 승인, 로그 마스킹 |
| AI 출력 스키마 불일치 | Pydantic 검증, 제한된 재시도, 실패 원인 표시 |
| Agent가 위험한 명령 실행 | 셸 없음, allowlist, workspace 경로 제한, 최소 권한, 실행 승인 |
| 무한 수정 반복 및 비용 폭증 | 재시도·호출 수·토큰·시간·비용 상한 |
| 데이터시트/외부 문서의 프롬프트 인젝션 | 비신뢰 입력 표시, 도구 권한과 분리, 코드 계산 diff, 승인 게이트. (v1.4) 데이터시트 요약은 정해진 표로만 읽고 판정은 코드가 함 |
| (v1.4) 데이터시트 링크 깨짐·자동 다운로드 차단·잘못된 문서 | 본문 품번 확인(품번/제품군/없음), 실패 시 원인과 'PDF 올리기' 안내, 출처 URL·해시 기록 |
| (v1.4) 데이터시트 요약 오류 | 모든 행에 쪽 번호, 「원문 확인함」 표시 전에는 미확인으로 표시, 대조 결과는 자동 검사 근거로만 표시 |
| 검증 성공의 과장 | 상태 3축 분리, 실행 증거 표시 |
| 승인 경로 중복 | proposal 단일 승인 경로(FR-16) |

## 14. 비기능 요구사항
- Windows 11 우선, 오프라인 프로젝트 조회/EDA 작업 지원(로컬 KiCad 설치 필요).
- API 키는 OS 보안 저장소 활용; 프로젝트 JSON/로그에 평문 저장 금지.
- LLM API 호출 전 전송 데이터 범위 표시.
- 생성 작업 취소, 진행 상태, 실패 재시도 제공.
- 프로젝트 파일 원자적 저장 및 revision 백업.
- 앱 충돌 시 마지막 승인 버전 복구 가능.
- 백엔드는 127.0.0.1에만 바인딩하고, 데스크톱 셸이 실행마다 만든 토큰이 없는 요청은 거부한다.
- 테스트는 Python pytest + TypeScript Vitest/Playwright.

## 15. Claude Code 개발 규칙
1. 첫 단계에서는 구현 전에 저장소 구조와 ADR-001(EDA 생성 전략)을 작성.
2. 한 번에 전체 앱 구현 금지. 단계 Gate를 통과한 뒤 다음 단계로 간다.
3. 모든 핵심 데이터 구조에 Pydantic schema와 테스트 작성.
4. AI 응답을 직접 KiCad 파일로 쓰지 말고 IR 검증 후 변환.
5. KiCad CLI 실행은 subprocess 인자 배열로 수행; 쉘 문자열 연결 금지.
6. 검증되지 않은 부품 핀맵·정격은 `unverified`로 기록.
7. 생성 파일의 Netlist와 IR의 연결 그래프를 비교.
8. 작업 종료마다 변경 파일, 테스트 결과, 미해결 위험, 다음 단계 보고.
9. AI Agent는 allowlist 도구만 사용하고 격리 작업공간에서 먼저 실행.
10. 사용자의 명시적 승인 전에는 원본 회로와 제조 산출물을 확정하지 않음.
11. 모든 Agent 실행에는 시간·모델·도구 버전·diff·검증 증거를 기록.
12. (v1.2) 원본 반영은 Revision Service만 한다. 다른 모듈은 원본 파일을 직접 쓰지 않는다.

## 16. Claude Code 첫 실행 프롬프트 (Phase 0, 완료됨)
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
- [x] 프로젝트 생성·저장·재열기 (Phase 1)
- [ ] 요구사항 구조화 스키마 검증 (Phase 2b)
- [ ] AI 설계안 승인/거부 (Phase 2)
- [x] JSON/Markdown 내보내기 (Phase 1)
- [x] LED 회로 .kicad_sch 생성 및 KiCad 열기 (Phase 0)
- [x] 추출 Netlist와 IR 연결 구조 일치 (Phase 0)
- [x] ERC 결과 및 경고 표시 — UI 표시 (Phase 1)
- [ ] 승인된 변경의 revision 기록/복원 (Phase 1 이력·복원 완료, Phase 2 승인 남음)
- [x] 오류·미검증 항목 명시 (Phase 1)

## 18. 결정 사항
| 항목 | 상태 |
|---|---|
| 지원 KiCad 메이저 버전 | **결정: 10.0.x** (ADR-002) |
| Windows 단일 플랫폼 우선 여부 | Windows 11 우선 유지. 개발·CI는 Linux에서도 동작 |
| LLM 제공자 및 API 키 입력 방식 | **결정: Google Gemini 기본, AI는 선택 기능.** 키는 OS 보안 저장소(keyring) 또는 `GEMINI_API_KEY` (ADR-004). **(v1.6)** Claude 추가: API 키(keyring, `ANTHROPIC_API_KEY`) 또는 Anthropic CLI 로그인 |
| 여러 보드 지원 | **결정: 프로젝트 → 보드 → 페이지** (사용자 결정, 2026-11-04) |
| 데이터시트 출처 | **결정: KiCad 심볼 링크 + 직접 올리기** (사용자 결정, 2026-11-04). 유통사 API(DigiKey·Mouser)와 AI 검색은 쓰지 않음. 필요해지면 키를 받아 추가 (ADR-005) |
| KiCad 생성 어댑터 최종 채택 | **결정: 직접 생성기** (ADR-001) |
| 템플릿의 검증/승인 책임과 출처 관리 정책 | **결정: 템플릿은 저장소 코드(검토·버전), 근거는 제조사 데이터시트 응용 회로의 절, 쪽 번호는 검토자가 기록, 템플릿 부품은 needs_review로 시작, verified는 엔지니어 검토 기록만** (ADR-007) |
| 선택 근거와 기술 검토 | **결정: 근거는 IR `decisions`(누가 정했는지 5종), 기술 검토는 AI가 아닌 코드 계산, 근거 리포트는 Markdown(앱·`outputs/rationale.md`·`aics report`)** (ADR-011) |
| Agent 도구 호출 방식과 상한 | **결정: 단계마다 JSON 행동 하나(공급자 중립), 기본 상한 LLM 12회·토큰 300,000·900초·도구 30회·재시도 2회** (ADR-008) |

## 19. 변경 이력
### v1.9 (2026-11-05)
- IR 0.7.0: `decisions`(주제·선택·누가 정했는지·이유·원래 요청·계산·출처·관련 부품). patch 연산 `set_decision`·`remove_decision`, 제안 비교 표에 바뀐 결정. AI 설계는 부품마다 이유와 근거 종류를, 템플릿은 데이터시트 절을 결정으로 남긴다. 「제안」 탭에서 엔지니어가 직접 이유를 적을 수 있다.
- 계산 기반 기술 검토(`circuit_core/engineering.py`): 「검토」 탭 맨 위, `GET …/technical-review`.
- IR 0.8.0: `Net.voltage`·`Net.load_current`(계산 입력, 그리기에는 안 씀), patch 연산 `set_net_spec`, 「검토」 탭의 「전원 조건」 표(제안으로 반영). 레귤레이터 발열을 실제 부하 전류로, 레귤레이터 최대 전류, 다이오드 전류·역전압, I2C 풀업, 크리스탈 부하 콘덴서 규칙 추가. AI 설계는 `rails`로 전원 조건을 낸다.
- 근거 리포트(`circuit_core/rationale.py`): 「문서 · BOM → 근거 리포트」, 생성 결과물 `rationale.md`, `aics report`. 샘플 보드 3종에 결정과 리포트를 넣음.
- 회로도 작성 기능 전반의 근거 리포트: `docs/schematic-rationale-report.md`.
- 회로도 그리기(ADR-009 추가 12~15): PWR_FLAG를 레일 전원 심볼과 같은 연결점에, IC 디커플링 콘덴서를 전원 핀 옆에 선으로, 48자를 넘는 주석은 여러 줄로, 위·아래를 향한 커넥터는 1번 핀이 왼쪽에 오게 반전.

### v1.8 (2026-11-05)
- IR 0.5.0: `notes`(영문 한 줄, 부품 묶음 또는 Net 하나에 붙음). patch 연산 `set_note`·`remove_note`, 제안 비교 표·Markdown 문서에 주석. 템플릿은 영문 캡션을 주석으로 붙이고, AI 설계는 `schematic_notes`로 블록·Net 주석을 낸다. 생성기는 KiCad 기울임 텍스트로 그린다(KiCad 재저장 무손실).
- 배치: 크리스탈은 두 선 아래에 눕히고 양 끝에서 부하 콘덴서가 바로 GND로, 전원 전용 커넥터는 벌크 콘덴서·전원 LED와 한 블록, 위를 향한 핀의 GND 콘덴서는 핀 옆에 세움, IC는 세부 윤곽(몸체·라벨 띠·전원 심볼)으로 충돌 검사, 배선 최대 길이 35.56mm.
- 레귤레이터 블록: 7805·AMS1117 같은 레귤레이터의 입출력 레일은 블록 안에서 선으로(콘덴서는 선에서 GND로, 보호 다이오드는 위로, 전원 LED는 세로), 레일 묶음마다 전원 심볼 하나.
- 샘플 보드 3종(`examples/atmega328p-led-board`, MCU 없는 `examples/ne555-led-board`, `examples/lm7805-24v-to-5v`).
- KiCad에서 옮긴 배치 가져오기(외부 수정 가져오기 1단계, ADR-010): 출력 회로도의 위치·회전·반전을 `AICS_ID`로 짝지어 `move` 제안으로. IR 0.6.0 `Placement.mirror`. 생성기의 회전·반전 순서를 KiCad와 같게(회전 후 반전) 바로잡음.
- Windows 설치 파일: PyInstaller 백엔드(Python 동봉) + electron-builder NSIS, 빌드 스크립트와 GitHub Actions 워크플로(ADR-003 갱신).

### v1.7 (2026-11-05)
- Phase 3 완료: 다중 유닛 심볼, 겹친 숨은 핀, 검토된 템플릿 4종, 템플릿 넣기(제안), AI 설계의 템플릿 우선, 부품 출처(IR 0.4.0 `Component.origin`), 엔지니어 검토 기록(상태 3축의 세 번째 축).
- Phase 4 완료: 제한된 AI EDA Agent(FR-13·14·15). 9.1절에 PLANNING 일시 상태와 범위 재승인 행동 추가, 비용 상한 기본값 확정.
- 18장: 템플릿 정책·Agent 방식 결정.

### v1.6 (2026-11-04)
- AI가 쓰는 사람용 글(설계 요약, 검토 의견, 요구사항 정리, 데이터시트 요약)을 한국어로.
- 검토 반영 수정안: 반영할 의견 고르기, 직접 요청, 이전 제안 대비 변경 표시(데스크톱 앱의 prompt() 제거).
- 회로도 그리기: 연결 기반 블록 배치, 전원·GND는 KiCad 전원 심볼, 가까운 신호 핀은 직접 배선(다른 Net과 닿지 않는지 검사 후), Net마다 이름 라벨 유지, 커넥터는 좌우 반전으로 1번 핀이 위, 두 선 사이 부품(종단 저항)은 두 선 사이에 세움. PWR_FLAG는 직렬 부품(인덕터·다이오드 등)으로 전원이 들어오는 Net에도.
- AI 공급자에 Claude 추가(API 키 또는 Anthropic CLI 로그인), 화면의 「받는 곳」 안내는 고른 공급자 이름.

### v1.5 (2026-11-04)
- Phase 2 완료(2a 변경 흐름, 2b AI 설계·검토). FR-03·04·09·16 완료, FR-23은 회로도 범위 완료.
- AI 설계 방식 결정(1.1, ADR-006). 구현 API와 patch 연산 이름(8, 9장).
- 생성기 항목 일부를 Phase 3에서 앞당김(no-connect, PWR_FLAG, 파생 심볼). IR 부품 핀에 `no_connect`.

### v1.4 (2026-11-04)
- 데이터시트 수집·활용 추가(1.1, FR-12 확장, FR-24·25, 7장 워크스페이스 `datasheets/`, 8장 API, 10장 L3 사전 점검, 13장 위험, 18장 결정, ADR-005).
- Phase 1.6 추가(완료). Phase 2b·3에 데이터시트 근거 사용을 명시.
- IR 스키마 0.3.0: 부품 `manufacturer`(선택). BOM에 Manufacturer 열.

### v1.3 (2026-11-04)
- 제품 비전 추가: 하나의 입력 창에서 회로도·아트웍·펌웨어(1장). Phase 5를 그 방향으로 재정의.
- 프로젝트→보드→페이지 구조(1.1, 7장), 보드 단위 API(8장).
- FR-18~23 추가: 여러 보드·페이지, PDF 회로도 카피 1·2단계, 보유 자재 우선 사용, AI 대체 추천, 통합 입력 창.
- AI 공급자 결정: Gemini 기본, 선택 기능, 키는 OS 보안 저장소(18장, ADR-004).
- Phase 1.5 추가(완료).

### v1.2 (2026-11-04)
- 승인 경로를 proposal 하나로 통일하고 `agent-runs/{id}/approve`를 삭제.
- 상태 3축(승인/자동 검사/엔지니어링 검토) 정의(10.1절).
- Agent 상태 머신 보완: REJECTED, CANCELLED, STALE, 재시도 루프 추가, DRAFT_REQUIREMENTS 제거, ROLLED_BACK을 revision 복원으로 대체.
- 승인 범위(연산 + 대상 ID)와 "설계 의미를 바꾸는 수정" 정의(9.0절).
- 격리 작업공간을 구조적 격리로 정의, Gateway 도구 추가(`read_revision`, `validate_ir`, `diff_ir`)와 경로 제한, 자체 도구 스키마 + 공급자 어댑터.
- diff는 코드가 계산. 프롬프트 인젝션은 구조적으로 대응.
- 비용 상한 단위 정의.
- 6장 IR 예시의 LED 핀을 번호(1=K, 2=A)로 수정.
- 7장: `.kicad_pro` 동반 생성, `outputs/` 의미와 manifest, 워크스페이스 위치. 7.1장: 외부 수정 감지, 작업 기록 저장 방식.
- 12장: Phase 2를 2a(LLM 없는 변경 흐름)와 2b(LLM)로 나누고 Phase 4가 2a 위에 서도록 순서 정리. Phase 1 범위 구체화. Phase 0 완료 표시.
- 18장: KiCad 10.0.x와 직접 생성기 채택을 결정으로 기록.

### v1.1 (2026-11-04)
- 변경: AI EDA Agent 계획/실행/관찰 계층 추가, 도구 실행 권한 제한, 승인 게이트, 반복 검증, 작업 감사 로그, PCB/FW 확장용 아티팩트 모델.
- 유지: Circuit IR 단일 기준 원본, Markdown 문서화, KiCad 생성 PoC 우선, 단계별 Gate, ERC와 엔지니어링 검증 구분.
- 외부 사례: 사용자가 공유한 AI Times 기사(https://www.aitimes.com/news/articleView.html?idxno=216989) 및 관련 AI 기반 물리적 엔지니어링 워크플로의 아이디어를 참고. 특정 모델 성능이나 실물 제작 성과는 본 프로젝트에서 검증된 사실로 간주하지 않음.

## 20. Claude Code 후속 개발 프롬프트 (Phase 4 완료 후 사용)
```text
docs/AI_Circuit_Studio_Development_Plan.md(v1.9)를 읽고 Phase 3·4 완료 여부를 실제 파일과 테스트로 검증하세요.
그다음은 Phase 5 착수 전 준비입니다. 아래 순서로 하되 각 단계는 사용자 확인 뒤 진행하세요.
1. 실제 AI(Gemini/Claude)로 AI 설계·Agent를 돌려 성공률·단계 수·토큰을 기록하고, 프롬프트와 상한을 조정하세요(가짜 AI 결과를 성능으로 보고하지 마세요). AI가 부품마다 남기는 근거(이유·근거 종류·계산)가 채워지는지도 확인하세요.
2. 템플릿의 데이터시트 원문을 확인해 쪽 번호와 값을 검토 기록으로 남기고, 템플릿을 v2로 올리세요(쪽 번호를 추측하지 마세요).
3. (v1.8 완료) 사람이 KiCad에서 옮긴 배치를 다음 생성에 유지하는 기능(ADR-010). 다음은 KiCad에서 바꾼 연결 가져오기(별도 ADR).
4. Phase 5(PCB·펌웨어)는 각 기능의 검증 방법을 먼저 정한 ADR 없이 구현하지 마세요.
```
