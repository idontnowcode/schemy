# ADR-004: AI 공급자와 API 키 보관

- 상태: **채택(Accepted)** — Phase 1.5 구현과 함께
- 날짜: 2026-11-04
- 관련: 기획서 v1.3 1.1절(AI는 선택 기능), 4장(스택), 13장(보안), 18장(결정 사항), FR-20·FR-22

## 배경

사용자가 두 가지 AI 기능을 요청했다.
- PDF 회로도를 "회로도 MD v0.1"로 옮겨 적기(PDF 카피 2단계)
- 보유 자재 목록 안에서 대체 자재 추천

그리고 다음을 정했다. "2단계는 옵션으로 하고 AI 모델은 기본적으로 Gemini API. AI 연결도 옵션으로."
그래서 공급자, 키 보관 방법, 기본값, 그리고 AI 결과가 원본에 들어가는 경로를 정해야 했다.

## 결정

1. **AI는 선택 기능이고 기본은 꺼져 있다.** 꺼져 있으면 AI 경로는 `409 AI_DISABLED`를 돌려준다. 나머지 기능(IR 편집, KiCad 생성, 회로도 MD 가져오기, 보유 자재 대조)은 AI 없이 모두 동작한다.
2. **기본 공급자는 Google Gemini API다.** 공식 Python SDK `google-genai`(2.34.0)를 쓴다.
   - 기본 모델은 `gemini-flash-latest`이다. Google이 최신 Flash 모델로 이어 주는 이름이라, 모델이 바뀌어도 설정을 고칠 필요가 없다.
   - 설정 화면의 **모델 목록 불러오기**로 `generateContent`를 지원하는 모델만 보여 주고, 사용자가 Pro 계열 등으로 바꿀 수 있다.
   - 온도 0, 최대 출력 32768 토큰, 제한 시간 300초가 기본값이다.
   - PDF는 18MB 이하면 요청에 직접 넣고, 더 크면 Files API로 올린다. 앱이 받는 PDF는 50MB까지다.
   - 자재 추천은 JSON Schema로 응답 형식을 고정한다(`response_json_schema`).
3. **공급자는 교체할 수 있다.** 앱은 `ai_engine.providers.LlmProvider` 인터페이스(`generate`, `list_models`)만 쓴다. Gemini 코드는 `GeminiProvider` 한 곳에 있다. 공급자 오류는 앱 공통 코드(`AI_AUTH_FAILED`, `AI_QUOTA`, `AI_MODEL_NOT_FOUND`, `AI_UNAVAILABLE`, `AI_BAD_OUTPUT` 등)와 복구 안내로 바꾼다.
4. **API 키는 OS 보안 저장소에만 둔다.**
   - `keyring` 라이브러리로 Windows 자격 증명 관리자(macOS 키체인)에 서비스 이름 `ai-circuit-studio`로 저장한다.
   - 환경 변수 `GEMINI_API_KEY`(또는 `GOOGLE_API_KEY`)가 있으면 그것을 먼저 쓴다. 보안 저장소를 쓸 수 없는 환경(CI, 일부 Linux)에서는 이 방법만 쓸 수 있다.
   - 키는 프로젝트 파일, SQLite, 작업 이력, AI 실행 기록, 로그 어디에도 쓰지 않는다. API는 키 값을 돌려주지 않고 "설정됨/없음"과 출처만 알려 준다.
5. **보내기 전에 보여주고 확인을 받는다.**
   - PDF 변환: 파일 이름·크기·공급자·모델을 보여 주고 확인을 받은 뒤에만 보낸다.
   - 자재 추천: 보낼 요청 내용(대상 BOM 줄과 후보 자재)을 `/inventory-suggest/preview`로 먼저 보여 준다.
6. **AI 결과는 사용자 확인 없이 저장하지 않는다.** Phase 2a의 proposal이 생기기 전까지는 다음 방식을 쓴다.
   - PDF 변환 결과(회로도 MD)는 1단계와 같은 가져오기 규칙으로 IR을 만들어 편집기에 불러오기만 한다. 핀 번호는 KiCad 라이브러리로만 정한다. 사용자가 검토하고 **저장**해야 revision이 생긴다.
   - 자재 추천은 보유 자재 목록에 있는 id만 받아들이고(없는 id는 버리고 보고), 사용자가 고른 것만 적용한다.
   - 두 흐름은 Phase 2a에서 proposal 승인 경로로 옮긴다(기획서 1.1절 v1.3 항목).
7. **호출마다 실행 기록을 남긴다.** 보드 폴더의 `ai_runs/`에 시각, 공급자, 모델, 입력 요약(파일 크기·SHA-256), 사용 토큰, 종료 이유, 결과 요약을 JSON으로 쓴다. 보낸 PDF와 AI가 만든 Markdown 사본은 `imports/`에 둔다.

## 추가 결정 (2026-11): Claude 공급자와 로그인

사용자 요청: "Claude로도 AI 모델을 사용할 수 있었으면 좋겠음. API 키가 아니라 로그인하는 식으로."

1. **두 번째 공급자로 Anthropic Claude API를 추가한다.** 공식 Python SDK `anthropic`(1.x)을 쓰고, 코드는 `ClaudeProvider` 한 곳에 있다.
   - 기본 모델은 `claude-opus-5-5`, 설정에서 공급자를 바꾸면 그 공급자의 기본 모델로 바뀐다.
   - 응답은 스트리밍으로 받는다(큰 `max_tokens`에서 HTTP 시간 초과를 피함). JSON 응답은 structured outputs(`output_config.format`, 모든 object에 `additionalProperties: false`)로 고정한다. PDF는 base64 `document` 블록으로 보낸다.
   - 현재 Claude 모델은 항상 사고(thinking)하므로 온도는 보내지 않고, `output_config.effort`를 `high`로 둔다.
   - 안전 분류기가 요청을 거절하면 같은 호출 안에서 Anthropic이 권하는 모델로 다시 돌리도록 `fallbacks: "default"`(베타 `server-side-fallback-2026-07-01`)를 켠다. Haiku에는 서버 쪽 대체가 없어 보내지 않는다. 그래도 거절되면 `AI_REFUSED`.
   - 오류 코드: 401/403 → `AI_AUTH_FAILED`, "credit balance" → `AI_BILLING`, 404 → `AI_MODEL_NOT_FOUND`, 429 → `AI_QUOTA`, 5xx/529 → `AI_UNAVAILABLE`.
2. **로그인은 Anthropic CLI의 `ant auth login`을 쓴다.** 설정의 **Claude 로그인**이 `ant auth login`을 인자 목록으로(셸 없이) 백그라운드에서 실행하고, 브라우저에서 Claude Console 계정으로 로그인한다. 토큰은 Anthropic CLI 폴더(`%APPDATA%\Anthropic`, `~/.config/anthropic`)에 저장되고 `anthropic` SDK가 직접 읽는다. 앱은 활성 프로필 파일이 있는지만 확인하고 토큰 파일을 열지 않는다.
   - claude.ai 구독(Pro/Max) 로그인은 Anthropic 정책상 다른 앱이 쓸 수 없다. 그래서 로그인은 Console 계정(API 크레딧 과금)이다. 이 점을 설정 화면과 README에 적었다.
   - API 키(OS 보안 저장소 또는 `ANTHROPIC_API_KEY`)가 있으면 로그인보다 우선한다. SDK의 인증 순서와 같다.
   - CLI가 없으면 `409 ANT_CLI_NOT_FOUND`와 설치 안내를 돌려준다. 이때도 API 키로 쓸 수 있다.
3. 화면의 "받는 곳" 안내와 버튼 설명은 선택한 공급자 이름을 쓴다.

검증: `tests/test_ai_claude.py`. 실제 `anthropic` SDK를 가짜 HTTP 전송으로 돌려 요청 모양(문서 블록, 닫힌 스키마, 대체 설정·베타 헤더, 온도 없음), 스트리밍 결과, 거절, 오류 코드 변환, 모델 목록을 확인한다. 로그인 프로필 감지(경로 조작 이름 거부 포함), CLI 실행 인자, 백엔드 설정 전환도 확인한다. 실제 Claude API 호출은 키가 없어 이 환경에서 하지 않았다.

## 추가 결정 (2026-11): ChatGPT 계정 로그인

사용자 요청: "api도 chatgpt나 claude를 사용할 수 있게 해줘. 단, API 키를 사용하는 것이 아니라 내 계정을 로그인하는 방식으로."

1. **세 번째 공급자로 OpenAI ChatGPT를 추가하고, API 키 없이 "Sign in with ChatGPT"로만 쓴다.** OpenAI가 오픈소스·로컬 앱용으로 공개한 방식이며, 사용량은 사용자의 ChatGPT 요금제에서 나간다. 앱 등록이나 OpenAI 승인은 필요 없다. 코드는 `ai_engine/chatgpt.py` 한 곳에 있다.
   - 처음 로그인: 브라우저로 `auth.openai.com/api/accounts/authorize`를 연다. `client_id=dynamic_agent_client`, 앱 이름 `agent_name_hint=AI Circuit Studio`, 이 PC의 `ext_agent_host_id`(`urn:uuid:…`, 한 번 만들어 계속 씀), PKCE(S256), `state`, `nonce`, 범위 `openid profile email offline_access resource.invoke chatgpt.tokens.use.direct`, `resource=https://api.openai.com/v1`.
   - 돌아오는 주소는 `http://127.0.0.1:1455/auth/callback`이다(1455가 쓰이고 있으면 빈 포트, 경로는 그대로). 콜백에서 `state`를 확인하고, 발급된 `client_id`(`oaiapp_…`)를 저장한다. `dynamic_agent_client`는 저장하지 않는다.
   - 다시 로그인할 때는 저장한 `client_id`와 이전 ID 토큰(`id_token_hint`), 이메일(`login_hint`)을 보낸다. 콜백이 다른 `client_id`를 주면 거부한다.
   - 코드 교환은 `auth.openai.com/api/accounts/oauth/token`에 폼으로 보낸다(클라이언트 비밀 없음). ID 토큰은 OpenAI 공개 키(JWKS)로 서명을 검증하고 발급자·대상(`client_id`)·만료·`nonce`를 확인한다. 같은 계정 재로그인이면 `sub`가 같은지도 본다.
   - 받은 범위에 `chatgpt.tokens.use.direct`가 없으면 로그인은 되었어도 요금제 사용이 꺼진 것으로 보고 설정 화면에 그렇게 알린다.
2. **토큰은 사용자 설정 폴더의 `chatgpt.json` 한 파일에 둔다.** Windows `%APPDATA%\AI Circuit Studio`, macOS `~/Library/Application Support/AI Circuit Studio`, 그 밖은 `~/.config/ai-circuit-studio`.
   - 파일은 임시 파일에 쓴 뒤 바꿔치기하고, 권한은 소유자만(0600)이다. OpenAI 문서가 권하는 방식이다. Claude 로그인 토큰을 Anthropic CLI가 자기 폴더에 두는 것과 같은 성격이다.
   - 프로젝트 폴더, SQLite, AI 실행 기록, 로그에는 쓰지 않는다. 콜백 주소에는 인증 코드가 실리므로 콜백 서버는 요청 로그를 남기지 않는다. API는 이메일·로그인 여부·요금제 사용 가능 여부만 돌려준다.
   - 접근 토큰은 1시간짜리다. 만료 5분 전이면 refresh token으로 새로 받고, 새 refresh token으로 바로 바꿔 저장한다(한 번 쓴 refresh token은 다시 못 씀). 갱신이 거부되면 로그인을 지우고 다시 로그인하라고 안내한다.
   - 로그아웃하면 refresh token을 OpenAI에 폐기 요청하고 파일에서 토큰을 지운다. 발급된 `client_id`와 호스트 id는 남겨 다음 로그인 때 다시 쓴다.
   - 발급된 `client_id`는 처음 등록한 계정에 묶인다. 그래서 **다른 계정으로 바꾸기**(`POST /api/ai/chatgpt/logout?forget=true`)는 토큰과 `client_id`를 함께 지우고, 다음 로그인 때 새로 등록한다. 호스트 id는 그대로 둔다.
3. **추론은 `POST api.openai.com/v1/responses`에 `store: false`, `stream: true`로 보낸다.** 이 경로가 받지 않는 `max_output_tokens`, `temperature`는 보내지 않는다. JSON 응답은 `text.format`의 `json_schema`로 요청하고, PDF는 `input_file`, 그림은 `input_image`로 넣는다. 모델 목록은 `GET /v1/models`에서 `visibility`가 `list`인 것만 보여 준다. 기본 모델은 `gpt-6.1-sol`이다.
4. **오류 코드:** `subscription_sharing_usage_limit_exceeded`·429 → `AI_QUOTA`(ChatGPT 설정 → 사용량에서 한도 확인 안내), `…_user_not_eligible` → `AI_AUTH_FAILED`(요금제 안내), `…_unavailable`·503 → `AI_UNAVAILABLE`, `…_unsupported_capability` → `AI_REQUEST_FAILED`, 401 → `AI_AUTH_FAILED`(다시 로그인).
5. **API 키 입력은 없다.** `PUT /api/ai/key/chatgpt`는 `409 LOGIN_ONLY`. 로그인은 `POST /api/ai/chatgpt/login`(브라우저 열기, 주소도 돌려줌), 상태는 `GET`, 로그아웃은 `POST /api/ai/chatgpt/logout`.

**Claude는 구독 계정(Pro/Max) 로그인을 넣지 않는다.** Anthropic은 claude.ai 구독의 OAuth 토큰을 Claude Code·claude.ai 밖의 다른 제품이나 Agent SDK에서 쓰는 것을 금지하고, 다른 앱은 API 키(Console) 인증을 쓰라고 한다. 그래서 Claude 쪽 "내 계정 로그인"은 이미 있는 Claude Console 로그인(`ant auth login`, API 크레딧 과금)으로 남긴다.

검증: `tests/test_chatgpt.py`. 가짜 auth.openai.com·api.openai.com 서버로 첫 등록(요청 인자, `state` 확인, 발급 `client_id` 저장, ID 토큰 검증, 0600 파일), 재로그인(`id_token_hint`, 다른 `client_id` 거부), 토큰 갱신과 교체, 로그아웃 폐기, 요금제 범위 없음, 서명 위조·`nonce`·대상·만료 거부, Responses 스트리밍 결과와 요청 본문(보내면 안 되는 필드 없음), 오류 코드 변환, 모델 목록, 백엔드 경로(`LOGIN_ONLY` 포함)를 확인한다. **실제 ChatGPT 계정으로는 로그인해 보지 않았다**(브라우저와 사용자 계정이 필요).

출처:
- OpenAI, Sign in with ChatGPT for open-source clients: https://developers.openai.com/siwc/token-sharing-open-source (sign-in, token-reference, models-and-inference, errors-and-recovery, preview-limitations, profiles-and-sessions 하위 문서)
- OpenAI, UI/UX guidelines: https://developers.openai.com/siwc/ui-ux-guidelines
- Anthropic, Legal and compliance (Claude Code 문서의 인증 정책): https://code.claude.com/docs/en/legal-and-compliance

연결 시험(2026-11): 설정 › 모델의 **연결 시험**은 고정 문장 한 줄("Connection test from AI Circuit Studio. Reply with the single word OK.")만 고른 공급자·모델에 보내 키·로그인, 모델 이름, 네트워크, 사용 한도를 실제 작업 전에 확인한다. 보내기 전에 받는 곳·모델·보낼 문장을 보여 주고 확인을 받는다. 회로·파일·프로젝트 정보는 보내지 않으므로 AI 기능이 꺼져 있어도 쓸 수 있다(키나 로그인은 필요). 생각하는 모델도 짧게 답하도록 출력 한도는 1024 토큰. 응답에 키가 들어가지 않는다(`tests/test_ai.py`, e2e `boards.spec.ts`). 실제 공급자로는 아직 눌러 보지 않았다.

## 검토한 다른 안

| 안 | 판단 |
|---|---|
| Anthropic Claude API | 처음에는 사용자가 Gemini를 기본으로 지정해 넣지 않았다. 2026-11 사용자 요청으로 두 번째 공급자로 추가했다(위 추가 결정). |
| OpenAI API(키) | 사용자가 키 대신 계정 로그인을 원해, 2026-11에 ChatGPT 계정 로그인으로 추가했다(위 추가 결정). |
| 로컬 모델(Ollama 등) | 회로도 PDF를 읽는 비전 성능이 아직 부족하고, 사용자 PC 사양에 따라 달라진다. 사내 반출 제한이 있는 사용자에게는 나중에 후보가 될 수 있다. |
| 키를 앱 설정 파일에 저장 | 기획서 13장(키는 OS 보안 저장소)에 어긋나므로 쓰지 않는다. |

## 검증

- `tests/test_ai.py`(가짜 공급자와 SDK 대역 사용):
  - AI가 꺼져 있거나 키가 없으면 거부
  - PDF가 아니면 거부
  - 키 값이 API 응답, SQLite 인덱스, AI 실행 기록(`ai_runs/`)에 나오지 않음
  - 보안 저장소가 없으면 `GEMINI_API_KEY`를 안내하고, 환경 변수 키를 씀
  - 앱이 쓰는 프롬프트가 `docs/prompts/pdf-schematic-to-markdown.md` 본문과 같음
  - 자재 추천이 목록에 없는 id를 버림
  - SDK 오류 코드가 앱 오류 코드로 바뀜
- `desktop/e2e/boards.spec.ts`: 설정 화면과 AI가 꺼진 상태의 안내.

## 남은 위험

- **실제 Gemini API로는 아직 한 번도 호출하지 않았다.** 개발 환경에 키가 없고 `ai.google.dev`에 접근할 수 없었다. SDK 호출 형태는 `google-genai` 2.34.0 소스와 타입으로 맞췄지만, 실제 응답 형식·오류·토큰 한도는 키를 넣고 확인해야 한다.
- **PDF 변환 정확도는 실제 회로도로 측정하지 않았다.** IC 핀 번호, 선 교차와 접속 구분, 숨은 전원 핀에서 틀릴 수 있다. 가져오기 단계의 라이브러리 대조와 "검토 필요" 표시가 1차 안전장치다.
- **Windows 자격 증명 관리자에 실제로 저장해 보지 않았다.** 개발 컨테이너에는 보안 저장소가 없어 환경 변수 경로만 확인했다.
- **ChatGPT 로그인은 실제 계정으로 해 보지 않았다.** OpenAI 공개 문서대로 만들고 가짜 서버로만 시험했다. 이 방식은 OpenAI가 미리보기(preview)로 표시한 기능이라 요청 형식이나 제한이 바뀔 수 있고, PDF(`input_file`)와 JSON 형식 지정이 이 경로에서 그대로 되는지도 실제로 확인해야 한다.
- 회사 회로도를 외부 AI로 보내도 되는지는 사용자의 사내 규정 문제다. 앱은 보내기 전 확인만 한다.
