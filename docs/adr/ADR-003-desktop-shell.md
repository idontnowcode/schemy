# ADR-003: 데스크톱 셸과 로컬 백엔드 연결 방식

- 상태: **채택(Accepted)** — Phase 1 구현과 함께
- 날짜: 2026-11-04
- 관련: 기획서 v1.2 4장(스택), 5장(구성), 14장(비기능 요구사항)

## 배경

기획서는 Electron + React UI와 Python(FastAPI) 백엔드를 쓰는 로컬 데스크톱 앱을 요구한다. 그래서 다음을 정해야 했다.
- 두 프로세스를 어떻게 띄우고 연결하는가
- 다른 로컬 프로그램이 API를 호출하지 못하게 어떻게 막는가
- 앱이 종료될 때 백엔드를 어떻게 정리하는가

## 결정

1. **백엔드가 빌드된 UI를 직접 서빙한다.** Electron 창은 `http://127.0.0.1:<port>/`를 연다. UI와 API가 같은 출처라서 CORS 설정이 필요 없고, 개발 모드(Vite 프록시)와 데스크톱 모드에서 코드가 같다.
2. **백엔드는 127.0.0.1에만 바인딩하고 실행마다 새 토큰을 요구한다.** Electron이 무작위 토큰(32바이트)을 만들어 환경 변수 `AICS_TOKEN`으로 백엔드에 넘긴다. 명령줄 인자는 프로세스 목록에 보이므로 쓰지 않는다. 모든 `/api` 요청에는 `Authorization: Bearer` 헤더가 필요하다. UI 정적 파일은 토큰 없이 받을 수 있다(민감 정보 없음).
3. **토큰은 preload 브리지로만 렌더러에 전달한다.** URL·쿠키·localStorage에는 두지 않는다. 렌더러는 `contextIsolation: true`, `sandbox: true`, `nodeIntegration: false`이다. preload는 `token`, `platform`, `openPath`만 노출한다.
4. **포트는 백엔드가 고른다.** `--port 0`으로 빈 포트를 잡고, 준비되면 stdout에 `{"event":"ready","port":…,"url":…,"workspace":…}` 한 줄을 출력한다. Electron은 이 줄을 기다린다(최대 90초).
5. **종료는 stdin으로 알린다.** 백엔드는 `--exit-on-stdin-eof`로 실행되어 stdin이 닫히면 종료한다. Electron이 비정상 종료해도 파이프가 닫히므로 백엔드가 남지 않는다. Windows에서도 신호 처리 없이 동작한다.
6. **창 보호:** 새 창 열기를 거부하고, 백엔드 출처 밖으로의 이동을 막고, CSP를 적용한다(`script-src 'self'`). `openPath`는 백엔드가 알려준 워크스페이스 안의 경로만 연다.
7. **Phase 1은 소스에서 실행한다.** Electron이 `uv run --project <repo> python -m backend`로 백엔드를 띄운다. `AICS_BACKEND_PYTHON`으로 Python 경로를 바꿀 수 있다.

## 검증

- `tests/test_backend_server.py`: 토큰 없이 시작하면 거부되는지, ready 줄이 나오는지, 토큰이 없으면 401인지, stdin을 닫으면 종료되는지.
- `desktop/e2e/electron.spec.ts`(Electron 45, Xvfb):
  - 렌더러에 `require`와 `process`가 없음
  - 토큰 없이 보낸 fetch는 401
  - 토큰을 쓰는 UI로 프로젝트 생성이 동작함
  - 워크스페이스 밖 `openPath`는 거부됨

## 남은 결정

- **배포 패키징:** 설치 프로그램에 Python 런타임을 어떻게 넣을지 정해야 한다. 후보는 PyInstaller로 만든 단일 실행 파일, 내장형 Python 배포판, uv 동봉이다. Windows에서 직접 실행해 본 뒤 ADR로 정한다.
- **코드 서명과 자동 업데이트:** 배포 단계에서 정한다.
