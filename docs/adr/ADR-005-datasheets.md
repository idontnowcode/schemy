# ADR-005: 데이터시트 수집과 활용 방식

- 상태: **채택(Accepted)** — Phase 1.6 구현과 함께
- 날짜: 2026-11-04
- 관련: 기획서 v1.4 1.1절, FR-12·24·25, 10장 L3, 13장, 18장. ADR-004(AI 공급자)

## 배경

사용자 요청: "사용되는 IC가 메이커가 확실한 경우 datasheet를 자동으로 수집하여 회로 작성 또는 분석에 활용하는 기능."

사용자가 고른 범위:
- 출처: KiCad 심볼 링크 + 직접 올리기 (가입·키 불필요)
- 활용: 핀맵 대조, 정격 점검, 외부 부품 체크리스트, 회로 작성 시 참고
- 시점: 지금 바로 (Phase 2a 전)

조사한 사실(2026-11-04):
- KiCad 10.0.8 심볼 23,772개 중 19,970개(약 84%)의 `Datasheet` 필드에 http(s) 링크가 있다. IC 라이브러리는 거의 100%(예: STM32F1, CAN/LIN 인터페이스 100%, 리니어 레귤레이터 99%)이고, `Device`(R/C 등)는 12%다.
- 심볼 링크는 보통 제품군 데이터시트이고, 오래된 `http://` 링크가 많다.
- 이 개발 환경에서는 제조사 사이트와 DigiKey·Mouser API가 네트워크 정책으로 막혀 있다(403). 실제 다운로드는 사용자 PC에서 확인해야 한다.

## 결정

1. **"메이커가 확실하다"의 기준.** 부품에 제조사와 제조사 품번이 모두 있거나(`mpn`), KiCad 심볼이 특정 부품 전용(일반 라이브러리 `Device`, `Connector*`, `power` 등이 아님)이면서 링크나 품번이 있을 때(`symbol`)다. 나머지는 `generic`이며 자동으로 받지 않는다. 이를 위해 IR 부품에 `manufacturer`를 추가했다(스키마 0.3.0).
2. **출처는 두 가지뿐이다.**
   - KiCad 심볼의 링크: 보드의 심볼에 들어 있는 링크만 받는다. 서버는 요청받은 링크가 그 보드의 심볼 링크인지 확인하고, 아니면 거부한다(`URL_NOT_FROM_LIBRARY`). 사용자가 링크 목록을 보고 확인한 뒤에만 받는다.
   - 직접 올리기: 제조사 사이트에서 받은 PDF.
   - 유통사 API와 AI 웹 검색은 쓰지 않는다. 필요해지면 키를 받아 별도 출처로 추가한다.
3. **다운로드 규칙.** `http://` 링크도 HTTPS로 받는다(평문 다운로드로 되돌아가지 않음). 리디렉트 5회, 크기 50MB, 본문이 PDF(`%PDF`)여야 한다. 실패하면 원인과 "PDF 올리기" 안내를 보여 준다. 시스템 프록시 설정(HTTPS_PROXY)을 따른다.
4. **받은 파일이 맞는지 확인한다.** `pypdf`로 앞쪽 6쪽의 글자를 읽어 품번이나 심볼 이름이 있는지 본다. 결과는 품번 확인됨 / 제품군 확인됨(예: STM32F103C8T6 → STM32F103C8) / 품번이 안 보임 / 글자를 못 읽음 중 하나다. 확인 결과와 무관하게 파일은 보관하되, 상태를 화면에 표시한다.
5. **워크스페이스 공통 라이브러리.** 데이터시트는 설계가 아니라 부품에 속하므로 `<workspace>/datasheets/`에 한 번만 둔다. 같은 내용은 해시로 합친다. 한 품번이나 한 심볼은 데이터시트 하나에만 연결되고, 일반 심볼(`Device:R` 등)에는 연결하지 않는다.
6. **데이터시트는 요약을 거쳐서만 쓴다.** 앱은 PDF를 직접 해석하지 않는다. 쪽 번호가 붙은 "데이터시트 요약 MD v0.1"(핀 표, 절대 최대 정격, 권장 동작 조건, 외부 부품, 응용 회로 메모, 불확실 항목)을 쓴다. 요약은 코파일럿(프롬프트 복사 버튼 제공), 사람, 또는 선택 기능인 AI(Gemini, ADR-004)가 만든다. PDF 회로도 카피와 같은 구조다.
7. **판정은 코드가 한다.**
   - 핀맵: 패키지를 핀 수 → 풋프린트 이름 순으로 고르고, 못 고르면 묻는다. 핀 이름은 표기 차이(대소문자, `_1`, `#`/`~{}`, VSS=GND, 노출 패드)를 정규화해 비교한다.
   - 정격: 전원 핀이 연결된 Net의 이름에서 공칭 전압을 읽어(`+3V3`, `+5V`, `VCC_12V`) 절대 최대 정격·권장 조건과 비교한다. 이름에 전압이 없으면 판단하지 않는다.
   - 외부 부품: 데이터시트가 말하는 두 지점 사이에 같은 종류·값의 부품이 있는지 찾는다.
8. **결과의 지위.** 대조 결과는 "요약이 맞다면" 성립하는 자동 검사 근거다. 회로를 바꾸지 않고, 엔지니어링 검토 상태도 바꾸지 않는다. 요약에는 "원문 확인함" 표시가 따로 있다. 회로를 고치는 제안은 Phase 2a의 승인 경로로만 한다.
9. **회로 작성에 쓰는 방법.** 응용 회로 메모와 외부 부품 목록을 facts로 보관한다. Phase 2b의 설계안은 이를 쪽 번호와 함께 인용하고, 제안 후 같은 대조를 다시 통과해야 한다.

## 추가 결정 (2026-11): 데이터시트 찾기 — 제조사 주소 규칙, Mouser·DigiKey 검색, 제안 단계 연결

사용자 요청: "데이터시트를 찾아서 읽을 수 있게 기능 개발은 안돼?" 제안한 1(AI 설계 뒤 받기·요약·대조 연결), 2(제조사 주소 규칙), 3(Mouser·DigiKey API 검색)을 사용자가 승인했다. AI 웹 검색(4)은 이번에도 넣지 않는다(위 "검토한 다른 안").

1. **받을 곳(후보)은 세 종류이고, 모두 앱이 스스로 찾은 것이다.** 결정 2의 "심볼 링크만"을 넓힌다. 후보마다 시도할 주소 목록이 있고, 사용자가 부품마다 고른 뒤 확인해야 받는다. 서버는 그 보드(또는 제안)의 부품에 대해 앱이 찾은 후보만 받는다(`URL_NOT_FROM_LIBRARY`).
   - **유통사 검색**(`mouser`, `digikey`): 사용자 키로 품번을 검색해 받은 `DataSheetUrl`/`DatasheetUrl`. 품번이 같거나(정확) 더 긴 주문 품번인 결과만 쓰고, 주소는 공개 이름의 http(s)만 받는다(IP·localhost·계정 정보가 든 주소는 버림). 결과는 `datasheets/candidates.json`에 품번별로 남겨 모든 보드가 함께 쓴다.
   - **KiCad 심볼 링크**(`kicad_symbol`): 기존과 같다.
   - **제조사 주소 규칙**(`maker_site`): 제조사(부품의 `manufacturer` 또는 심볼 링크의 호스트)가 TI·ST·onsemi·Diodes·Nexperia이면 품번으로 주소를 만든다. 품번 전체에서 제품군 핵심까지 줄여 가며 최대 4개(TI는 제품군 이름을 먼저, 그리고 끝 두 자리 전압 코드를 뗀 이름도: TPS7A0233 → TPS7A02).
2. **주소 규칙의 근거는 KiCad 라이브러리 자체다.** KiCad 10.0.8 심볼 링크 19,970개에서 제조사마다 가장 많은 주소 형태를 골랐다(TI `lit/ds/symlink/<품번>.pdf` 2,739개·`lit/gpn/<품번>` 308개, ST `resource/en/datasheet/<품번>.pdf` 3,114개, Diodes `assets/Datasheets/<품번>.pdf` 283개, Nexperia `documents/data-sheet/<품번>.pdf` 368개, onsemi `pdf/datasheet/<품번>-d.pdf` 80개). 심볼 이름에 규칙을 적용해 심볼 자신의 링크가 나오는 비율은 TI 55%, ST 64%, Diodes 53%, Nexperia 34%, onsemi 27%였다. ADI(23%, 파일 이름이 품번이 아닌 경우가 많음)와 Microchip(문서 번호 주소)은 넣지 않았다.
3. **규칙으로 만든 주소는 받은 뒤 한 번 더 확인한다.** 읽을 수 있는 PDF인데 부품 품번도, 주소가 가리킨 제품군 이름도 없으면 저장하지 않고(`DATASHEET_WRONG_PART`) 다음 주소로 넘어간다. 제품군 이름만 있으면 "제품군 확인됨"으로 저장한다(예: TPS7A0233 → tps7a02.pdf).
4. **실패하면 다음 후보.** 고른 후보가 모두 실패하면 같은 부품의 다른 후보를 정해진 순서(DigiKey → Mouser → 심볼 링크 → 제조사 규칙)로 시도한다. 확인 창에 시도할 수 있는 호스트를 모두 보여 준다. 끝내 실패하면 제품 페이지나 마지막 주소를 **브라우저에서 열기**(데스크톱 앱은 `https://`만 시스템 브라우저로 연다)와 "PDF 올리기"를 안내한다. 사이트의 봇 차단을 우회하지 않는다(브라우저인 척하는 User-Agent를 쓰지 않음).
5. **유통사 키.** OS 보안 저장소에 `mouser`, `digikey-id`, `digikey-secret` 이름으로 저장하거나 환경 변수 `MOUSER_API_KEY`, `DIGIKEY_CLIENT_ID`, `DIGIKEY_CLIENT_SECRET`을 쓴다. API는 키 값을 돌려주지 않는다. 검색할 때 보내는 것은 품번뿐이고, 보내기 전에 품번 목록을 보여 주고 확인을 받는다.
   - Mouser: `POST https://api.mouser.com/api/v1/search/partnumber?apiKey=…`, 본문 `{"SearchByPartRequest": {"mouserPartNumber": 품번, "partSearchOptions": "None"}}`, 응답 `SearchResults.Parts[]`의 `ManufacturerPartNumber`·`DataSheetUrl`·`ProductDetailUrl`·`LifecycleStatus`. 공식 Swagger(`api.mouser.com/api/docs/v1`)로 확인했다. 명세에 오류 코드가 없어 잘못된 키는 오류 문구로 구분한다.
   - DigiKey: `POST https://api.digikey.com/v1/oauth2/token`(client_credentials, 토큰 10분, 재사용) → `POST https://api.digikey.com/products/v4/search/keyword`(헤더 `X-DIGIKEY-Client-Id`, `Authorization: Bearer`; 본문 `{"Keywords": 품번, "Limit": 10}`), 응답 `ExactMatches`·`Products`의 `ManufacturerProductNumber`·`DatasheetUrl`·`ProductUrl`·`ProductStatus.Status`. 개발자 포털의 공식 명세와 OAuth 문서로 확인했다.
6. **제안 단계에 연결한다(1번).** 「제안」 탭의 검사 결과에 같은 "데이터시트 찾기"가 나온다. 제안의 후보 회로로 부품을 고르고(`GET …/datasheets?proposal_id=`), 받은 뒤 요약이 없으면 **AI로 요약**(보낼 파일·받는 곳 확인)을 하고, **데이터시트 대조 다시 하기**(`POST …/proposals/{id}/recheck-datasheets`)로 증거의 데이터시트 부분만 다시 계산한다. KiCad 검사는 다시 돌리지 않는다. 승인 전에 AI 설계가 데이터시트와 맞는지 볼 수 있다.

검증:
- `tests/test_datasheet_search.py`: 품번 줄이기·제조사 이름 인식·주소 규칙·전압 코드, 외부 주소 제한, 가짜 Mouser·DigiKey 서버(요청 본문·헤더, 정확 품번 고르기, 잘못된 키, 토큰 재사용, 위험한 주소 버림), 검색 job → 후보 순서 → 고른 후보 실패 뒤 다음 후보로 받기, 모르는 후보 거부, 제안 기준 보고서와 다시 대조, 키 저장·삭제(값은 응답에 없음), 규칙 주소의 다른 문서 거부.
- `tests/test_backend_datasheets.py`(갱신): 후보 목록, 실패 시 제조사 규칙까지 차례로 시도.
- `desktop/e2e/datasheets.spec.ts`, `fixes.spec.ts`: 받을 곳 선택 상자(심볼 링크·TI 규칙), 키가 없으면 유통사 검색 꺼짐, 제안 화면의 데이터시트 찾기. `electron.spec.ts`: 브라우저 열기가 `https://` 외 주소를 거부.
- **실제 사이트**: 앱의 다운로드 코드로 TI 규칙을 확인했다(2026-11-05). DRV8871DDAR → `lit/gpn/drv8871`(제품군 확인됨), SN65HVD230DR → 품번 확인됨, TPS7A0233PDBVR → `tps7a02`, TLV70033DDCR → `tlv700`(제품군 확인됨). ST 사이트는 이 환경에서 정직한 User-Agent의 자동 요청에 응답하지 않았다(빈 응답). onsemi·Diodes·Nexperia는 이 환경에서 접속할 수 없어 받아 보지 않았다. Mouser·DigiKey는 키가 없어 실제 호출하지 않았다.

외부 부품의 조건(2026-11): 요약 MD 「외부 부품」 표의 설명 칸에서 종류(탄탈·세라믹·X7R·X5R·C0G·전해·폴리머)와 ESR 범위(「ESR 0.3~22 Ω」, 「ESR 0.1 Ω 이하」), 「최소·이상」을 읽는다. 찾은 부품의 사양(`Component.specs`)과 비교해, ESR이 범위 밖이거나 종류가 다르면 「사양 불일치」(필수면 경고), 사양이 비어 있으면 「부품 사양에 ESR을 적으면 확인합니다」라고 알린다. 「최소」면 더 큰 값도 맞는 것으로 본다(10 µF 최소에 22 µF). 실제 LM1117 요약으로 확인했다: C2(탄탈, ESR 미기재)는 「있음 + ESR을 적으세요」, ESR 0.005 Ω X7R로 바꾸면 「사양 불일치」.

실제 데이터시트로 돌려 본 결과(2026-11-05, `examples/i2c-sensor-3v3`, TI LM1117 SNOS412Q): 처음에는 핀맵이 「불일치 3」, 전압·외부 부품이 「확인 불가」였다. 데이터시트는 VIN·VOUT·ADJ/GND, KiCad 심볼은 VI·VO·GND로 이름이 달랐기 때문이다. 핀 이름 비교에 흔한 별칭(VIN = VI = IN, VOUT = VO = OUT), 겸용 이름(「ADJ/GND」는 고정 전압판의 GND), 같은 신호의 탭·중복 핀(SOT-223 TAB = VOUT, 상태 `same_as`)을 더했다. 품번 확인은 앞 6쪽만 읽어 TI 주문 품번(23쪽)을 놓쳤으므로 앞뒤 40쪽까지 읽는다(47쪽 3.3 s). 고친 뒤 핀맵 일치, 전압 정상, 필수·권장 콘덴서 있음, 품번 확인됨.

BOM 재고·가격 조회(2026-11): 같은 유통사 키로 「문서 · BOM」 탭에서 **유통사 재고·가격 조회**를 할 수 있다. MPN이 있는 BOM 줄만, 품번만 보낸다(job). 줄마다 유통사별 재고(수량), 가장 작은 수량 구간의 단가, 수명 상태(Active·NRND·Obsolete 등), 제품 페이지를 보여 주고, 재고가 필요 수량보다 적거나 단종·생산 종료·신규 설계 비추천이면 「확인할 점」에 적는다. 결과는 보드 폴더 `bom_availability.json`에 revision과 조회 시각과 함께 남고, 회로가 바뀌면 다시 조회하라고 알린다. 쓰는 필드: Mouser `PriceBreaks[{Quantity, Price, Currency}]`·`AvailabilityInStock`·`Availability`·`LifecycleStatus`·`IsDiscontinued`·`MouserPartNumber`, DigiKey `QuantityAvailable`·`UnitPrice`·`ProductStatus.Status`·`Discontinued`·`EndOfLife`(위 명세와 같은 문서). 가격은 조회 시점 값이고 주문 전에 유통사 페이지에서 다시 확인해야 한다. 실제 키로는 호출하지 않았다(가짜 서버로 시험).

요약 틀(2026-11): 「데이터시트」 탭의 **요약 틀 받기**는 그 부품의 제조사·품번과 회로의 핀 번호, 풋프린트 이름만 채운 빈 요약 MD를 내려 준다. 핀 이름·정격·쪽 번호는 비워 둔다. KiCad 심볼의 핀 이름을 채우면 핀 대조가 심볼을 자기 자신과 비교하게 되기 때문이다. 이름이 빈 핀 행은 가져올 때 건너뛰고 알리므로(`PIN_NAME_MISSING`), 채우지 않은 틀은 「핀 없음」으로 가져오지 않는다.

출처:
- Mouser API Swagger: https://api.mouser.com/api/docs/v1 (UI: https://api.mouser.com/api/docs/ui/index)
- DigiKey Product Information v4 KeywordSearch: https://developer.digikey.com/products/product-information-v4/productsearch/keywordsearch
- DigiKey OAuth: https://developer.digikey.com/documentation

## 검토한 다른 안

| 안 | 판단 |
|---|---|
| DigiKey·Mouser API로 품번 검색 | 처음에는 가입·키가 필요해 제외했다. 2026-11 사용자 승인으로 선택 기능으로 추가했다(위 추가 결정). |
| AI가 웹에서 데이터시트를 찾기 | 다른 문서를 찾을 위험이 크고 확인 비용이 든다. 제외. |
| PDF 표를 코드로 직접 파싱 | 데이터시트마다 표 형식이 달라 신뢰할 수 없다. 요약 MD + 코드 판정으로 대신한다. |
| AI가 대조까지 판단 | 판단 근거를 재현할 수 없다. AI는 요약만 만들고 판정은 코드가 한다. |
| 프로젝트별 `datasheets/` 폴더 | 같은 IC를 쓰는 프로젝트마다 다시 받고 다시 요약해야 한다. 워크스페이스 공통으로 바꿨다. 프로젝트 폴더를 다른 PC로 옮길 때 데이터시트를 함께 묶는 기능은 나중에 필요하면 추가한다. |

## 검증

- `tests/test_datasheets.py`: 품번 확인, 전압 읽기, 핀 이름 비교, 요약 파싱(오류·경고·±값·연결 표기), 핀맵·정격·외부 부품 판정, 패키지 선택, 노출 패드, 라이브러리(중복·연결 이전·삭제), 다운로더(로컬 서버: 리디렉트, PDF 아님, 404, 크기 제한, HTTPS 강제, `file://` 거부).
- `tests/test_backend_datasheets.py`: 보드 보고서, 올리기·요약·확인 표시, 심볼에 없는 링크 거부, 다운로드 실패 보고, AI 요약(가짜 공급자)·잘못된 응답, 회로도 MD의 제조사 열, 일반 심볼 미연결, 프롬프트 제공.
- `desktop/e2e/datasheets.spec.ts`: 화면에서 PDF 올리기 → 요약 가져오기 → 대조 결과 → 원문 확인 → 라이브러리 화면.

## 남은 위험

- **실제 사이트에서는 TI만 받아 보았다**(추가 결정의 검증 참고). 일부 사이트는 자동 다운로드를 막거나(ST는 이 환경에서 응답 없음), 링크가 제품 페이지로 바뀌었을 수 있다. 이때는 실패 원인, 브라우저에서 열기, "PDF 올리기"를 안내한다.
- **Mouser·DigiKey는 실제로 호출하지 않았다.** 명세대로 만들고 가짜 서버로 시험했다. 유통사의 데이터시트 링크가 자동 다운로드를 막는 경우도 있을 수 있다(확인하지 못함).
- **요약의 정확도.** 코파일럿·AI 요약은 틀릴 수 있다. 쪽 번호와 "원문 확인함" 표시로 사람이 대조하게 한다.
- **전압 판단의 한계.** Net 이름에 전압이 없으면 판단하지 않는다. 전압 강하, 전원 순서, 신호 핀의 입력 전압 한계는 보지 않는다.
- **패키지 자동 선택의 한계.** 핀 수와 풋프린트 이름이 같은 패키지가 여럿이면 고르지 않고 묻는다(현재는 풋프린트를 지정하는 방법만 있다).
