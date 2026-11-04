# AI Circuit Studio

자연어 회로 요구사항 → AI 설계안 → 사용자 승인 → Circuit IR(JSON) → Markdown 문서 + KiCad 회로도를 만드는 Windows 우선 데스크톱 앱입니다.
기획서는 [`docs/AI_Circuit_Studio_Development_Plan.md`](docs/AI_Circuit_Studio_Development_Plan.md)(v1.2)에 있습니다.

![AI Circuit Studio — 회로도·검증 화면](docs/images/phase1-schematic.png)

## 진행 상황

| 단계 | 상태 | 내용 |
|---|---|---|
| Phase 0 — KiCad 생성 PoC | ✅ 완료 | IR → `.kicad_sch`, ERC/Netlist/SVG, 생성 방식 A/B 평가 ([보고서](docs/phase0-report.md)) |
| Phase 1 — Core MVP | ✅ 완료 | 데스크톱 앱, 프로젝트 저장·복원, revision, 문서·BOM, KiCad 생성 job, 산출물 관리 ([보고서](docs/phase1-report.md)) |
| Phase 2a — 변경 흐름(LLM 없음) | 다음 | patch·diff·proposal·승인 게이트 |
| Phase 2b — AI 연동 | 대기 | LLM 공급자 결정 필요 |

AI 기능은 아직 없습니다. 지금은 사람이 Circuit IR(JSON)을 넣으면 앱이 검사하고, 저장하고, KiCad 프로젝트와 문서를 결정적으로 생성합니다.

## 데스크톱 앱 실행

필요한 것:
- Python 3.12 이상과 [uv](https://docs.astral.sh/uv/)
- Node.js 22 이상
- KiCad 10.0. Windows에서는 설치하면 자동으로 인식합니다. Linux 개발 환경에서는 Docker로 대신할 수 있습니다(아래 참고).

```bash
uv sync                                   # Python 의존성
cd desktop
npm install                               # UI·Electron 의존성
npm start                                 # 빌드 후 Electron 실행 (백엔드는 앱이 직접 띄움)
```

- 프로젝트는 `문서/AI Circuit Studio/projects/<id>/`에 저장됩니다. 위치를 바꾸려면 `AICS_WORKSPACE`를 설정하세요.
- 앱을 처음 쓸 때는 **회로 (IR)** 탭의 **예제 불러오기**에서 LED 예제를 저장하고, **회로도 · 검증** 탭에서 생성해 보세요.
- 생성 결과는 **KiCad에서 열기** 버튼이나 `outputs/schematic.kicad_pro`로 엽니다.

### 개발 모드 (브라우저)

```bash
uv run python -m backend --no-auth --port 8765     # 터미널 1: API (개발용, 인증 없음)
cd desktop && npm run dev                          # 터미널 2: http://localhost:5173
```

## KiCad에서 열기

바로 열어 볼 수 있는 예제 프로젝트가 [`examples/led-resistor-connector/`](examples/led-resistor-connector/)에 있습니다.

1. KiCad 10에서 **파일 → 프로젝트 열기**로 `led-resistor-connector.kicad_pro`를 엽니다.
2. 프로젝트 관리자에서 회로도를 엽니다.

**`.kicad_sch`만 단독으로 열지 마세요.** 단독으로 열면 KiCad가 프로젝트 없이 동작하면서 다른 프로젝트의 라이브러리 설정을 섞어 씁니다.
그러면 이 회로도와 관계없는 라이브러리(예: `mipi_phy`, `mipi_csi`)를 폴더에서 찾을 수 없다는 경고가 뜰 수 있습니다.
앱과 `aics generate`는 항상 `.kicad_sch`와 같은 이름의 `.kicad_pro`를 함께 만듭니다.

## KiCad·심볼 라이브러리 연결

kicad-cli는 다음 순서로 찾습니다.

1. `AICS_KICAD_CLI`: kicad-cli 실행 파일 경로
2. `PATH`의 `kicad-cli`
3. Windows 기본 경로 `C:\Program Files\KiCad\10.0\bin\kicad-cli.exe`
4. `AICS_KICAD_DOCKER_IMAGE`: KiCad 공식 Docker 이미지 (Linux 개발/CI용)

심볼 라이브러리는 다음 순서로 찾습니다.
1. `AICS_SYMBOL_DIRS`(여러 개면 OS 경로 구분자로 연결)
2. `KICAD10_SYMBOL_DIR`
3. OS별 기본 설치 경로. Windows는 `C:\Program Files\KiCad\10.0\share\kicad\symbols`입니다.

kicad-cli가 없으면 회로도 파일은 만들지만 ERC·Netlist·SVG 검사는 하지 않고, 결과를 **Not checked**로 표시합니다. 이것을 성공으로 표시하지 않습니다.

```bash
# Linux / CI (Docker)
docker pull kicad/kicad:10.0.8          # Docker Hub 요청 제한(429)에 걸리면 mirror.gcr.io/kicad/kicad:10.0.8
export AICS_KICAD_DOCKER_IMAGE=kicad/kicad:10.0.8
uv run aics kicad-info                  # → kicad-cli: 10.0.8 (docker: kicad/kicad:10.0.8)
```

Docker로 실행할 때 출력 폴더는 컨테이너의 `/work`로 마운트되고, 컨테이너는 네트워크 없이(`--network none`) 실행됩니다.

## 명령줄 도구 (`aics`)

앱 없이 IR 파일 하나를 KiCad 프로젝트로 바꿀 때 씁니다.

```bash
uv run aics generate examples/led-resistor-connector/circuit.json \
    --out my-led --symbol-dir tests/fixtures/kicad_symbols --check
```

종료 코드는 `0` 성공, `1` 생성 또는 검사 실패, `2` 입력 오류, `3` kicad-cli가 없어 **검사를 하지 않음**(성공 아님)입니다.

## 프로젝트 폴더 구조

```text
<워크스페이스>/
  index.sqlite             # 프로젝트 목록·작업 이력·앱 설정 (폴더에서 다시 만들 수 있는 캐시)
  .trash/                  # 삭제한 프로젝트 (복구 가능)
  projects/<project-id>/
    project.json           # 이름, 현재 revision
    requirements.md
    design_notes.md
    circuit.json           # 현재 Circuit IR
    revisions/0001.circuit.json, 0001.meta.json, …
    outputs/               # 마지막으로 생성한 revision의 산출물
      manifest.json        # 파일별 유형·revision·도구 버전·SHA-256·검사 상태
      schematic.kicad_pro / .kicad_sch / .svg / .net, erc.json, check_report.json
      circuit.md, circuit.json, bom.csv, symbol_sources.json
```

- 회로를 저장하면 내용이 바뀐 경우에만 새 revision을 만듭니다. 복원도 새 revision으로 기록하므로 이력이 지워지지 않습니다.
- 생성은 임시 폴더에서 진행합니다. 모두 성공했을 때만 `outputs/`를 교체합니다.
- 생성 이후 회로가 바뀌면 산출물을 **오래됨**으로 표시합니다.
- 생성된 파일을 KiCad 등에서 수정하면 **외부에서 수정됨**으로 표시하고, 다시 생성하기 전에 확인을 받습니다.

## 저장소 구조

```text
circuit_core/     Circuit IR 모델(Pydantic), L0/L1 규칙 검사, Markdown/JSON/BOM 생성
kicad_adapter/    .kicad_sch 생성기, 심볼 라이브러리 읽기, kicad-cli 실행, Netlist/ERC 파싱
backend/          FastAPI 로컬 API: 프로젝트·revision·생성 job·산출물 (127.0.0.1 + 토큰)
desktop/          Electron 셸 + React UI (Vite), e2e 테스트(Playwright)
examples/         예제 IR과 KiCad 프로젝트 (golden 파일 겸용)
experiments/      ADR 실험 기록 (SKiDL/schematizer)
tests/            Python 단위·API·KiCad 통합 테스트
docs/             기획서 v1.2, ADR, IR JSON Schema, 단계별 보고서
```

## 테스트

```bash
uv run pytest                                      # Python (KiCad 없으면 KiCad 테스트는 skip — 통과 아님)
AICS_KICAD_DOCKER_IMAGE=kicad/kicad:10.0.8 uv run pytest   # 실제 KiCad 포함
uvx ruff check .                                   # Python lint

cd desktop
npm run typecheck && npm test                      # TypeScript 타입 검사, Vitest
npm run build && npx playwright test e2e/app.spec.ts       # 브라우저 e2e (백엔드 실제 실행)
xvfb-run -a npx playwright test e2e/electron.spec.ts       # Electron 셸 e2e (Linux는 xvfb 필요)
```

`AICS_UPDATE_GOLDEN=1 uv run pytest`는 `examples/` 예제와 JSON Schema를 다시 씁니다. diff를 확인한 뒤 커밋하세요.

## Circuit IR 핵심 규칙

- **연결은 KiCad 심볼의 핀 번호로만 저장합니다.** 예를 들어 `Device:LED`는 1=K, 2=A입니다. 핀 이름은 근거로만 기록하고, 생성 전에 라이브러리와 대조해 다르면 파일을 만들지 않습니다.
- 부품의 모든 핀이 라이브러리 심볼과 정확히 일치해야 합니다. 빠진 핀, 없는 핀, 이름 불일치는 모두 오류입니다.
- IR 규칙 오류(중복 참조, 없는 핀 등)가 있으면 저장되지 않습니다. 라이브러리 핀 불일치는 저장은 되지만 회로도 생성이 거부됩니다.
- 배치(`layout`)는 연결과 분리되어 있습니다. 배치 때문에 서로 다른 Net이 맞닿으면 생성을 거부합니다.
- 생성된 심볼에는 숨김 필드 `AICS_ID`(IR component id)가 들어갑니다.

## 상태 표시 (기획서 v1.2 10.1절)

| 축 | 값 | 의미 |
|---|---|---|
| 자동 검사 | Not checked / Automated checks passed / failed | kicad-cli ERC와 Netlist ↔ IR 비교 결과. **설계가 맞다는 뜻이 아님** |
| 엔지니어링 검토 | 미확인 / 검토 필요 / Engineer reviewed | 사람만 바꿀 수 있음. 앱과 자동 검사는 바꾸지 않음 |
| 승인 | (Phase 2부터) | AI 제안의 승인/거부 |

## 현재 제약

- **Windows 11에서는 아직 실행해 보지 않았습니다.** 검증은 Linux 컨테이너(KiCad 10.0.8 Docker, Electron 45 + Xvfb)에서 했습니다.
- **설치 패키지가 아직 없습니다.** 지금은 소스에서 실행하며, 앱이 `uv`로 백엔드를 띄웁니다([ADR-003](docs/adr/ADR-003-desktop-shell.md)).
- KiCad 10.0.x만 지원합니다([ADR-002](docs/adr/ADR-002-kicad-version.md)).
- 생성기가 지원하지 않는 것: 파생 심볼(`extends`), 다중 유닛 심볼, 미러, 전원 심볼, 계층 시트, no-connect 표시. 만나면 오류를 냅니다.
- 연결은 핀마다 짧은 선과 Net 라벨을 붙이는 방식이라, 부품 사이를 직접 잇는 배선이 없습니다. 자동 배치는 단순 격자입니다.
- 실행 중인 생성 작업은 취소할 수 없습니다. 필드형 요구사항은 Phase 2에서 추가합니다.
