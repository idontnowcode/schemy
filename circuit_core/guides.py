"""Design guide ("설계 지침"): rules of practice that grow with use.

A *guide entry* says what to do when a kind of circuit is drawn ("an LDO's heat is (Vin - Vout) x I; check
it against the package"), why, how strongly (must / should / consider), which circuits it applies to,
and where it came from. Entries come from

* the app's built-in baseline (``SEED``), written like the reviewed templates: general practice with the
  app's own calculation rule named where one exists; no invented datasheet pages;
* people: an engineer's review ("다음에는 …"), a rejected proposal's reason, a revision request, or a
  guide written by hand;
* the AI: a review finding or a person's feedback rephrased as a guide. These start as *candidates*.

Only *adopted* entries are used: they are given to the AI when it designs, revises or reviews a circuit,
and they form the checklist of a proposal and of a board. A person adopts or retires an entry; every change
is kept in the history, so the guide is also a record of what was learned and when.

This module has no storage: the backend keeps the entries (``backend/guides.py``).
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Literal

from pydantic import BaseModel, Field

from .models import CircuitIR, Component
from .parts import category_of

GuideStatus = Literal["candidate", "adopted", "retired"]
GuideOrigin = Literal["seed", "engineer", "review", "ai_review", "feedback", "check"]
Strength = Literal["must", "should", "consider"]
SourceKind = Literal["datasheet", "app_note", "standard", "practice", "app_rule", "review", "feedback", "ai"]

TOPICS: dict[str, str] = {
    "general": "일반",
    "power": "전원",
    "ldo": "리니어 레귤레이터",
    "dcdc": "스위칭 레귤레이터",
    "protection": "보호 회로",
    "mcu": "MCU",
    "interface": "통신 인터페이스",
    "motor": "모터 구동",
    "led": "LED·표시",
    "clock": "클럭·크리스탈",
    "connector": "커넥터·배선",
    "analog": "아날로그",
    "schematic": "회로도 작성",
}
STRENGTH_LABEL = {"must": "필수", "should": "권장", "consider": "검토"}
CHECKS: dict[str, str] = {
    # Linked automated checks: the technical review's rule ids (circuit_core.engineering) and the quick fixes.
    "decoupling": "디커플링·입출력 콘덴서", "reg_heat": "레귤레이터 발열", "reg_dropout": "레귤레이터 드롭아웃",
    "reg_current": "레귤레이터 전류", "cap_voltage": "콘덴서 전압 정격", "polarity": "극성 부품 방향",
    "diode_current": "다이오드 전류", "diode_reverse": "다이오드 역전압", "led_current": "LED 전류",
    "led_series_r": "LED 직렬 저항", "led_direction": "LED 방향", "r_power": "저항 전력",
    "i2c_pullup": "I2C 풀업", "crystal_caps": "크리스탈 부하 콘덴서", "motor_vm": "모터 드라이버 VM",
    "motor_ilim": "모터 드라이버 전류 제한", "motor_heat": "모터 드라이버 발열", "unknown_voltage": "전압 모르는 전원 Net",
    "fuse_rating": "퓨즈 정격", "cap_dielectric": "콘덴서 유전체",
    "floating_input": "떠 있는 입력 핀",
}


class GuideSource(BaseModel):
    kind: SourceKind
    text: str = Field(max_length=500, description="e.g. 'TI DRV8871 데이터시트 8.2절', '보드 A rev 3 검토 (홍길동)'.")
    url: str | None = Field(default=None, max_length=500)
    ref: dict = Field(default_factory=dict, description="project_id / board_id / proposal_id / review_id / revision.")


class GuideScope(BaseModel):
    parts: list[str] = Field(default_factory=list, description="Words in a part's value, MPN or symbol (e.g. DRV88).")
    keywords: list[str] = Field(default_factory=list, description="Words in the requirements (e.g. 모터, 배터리).")


class GuideDraft(BaseModel):
    """What a person or the AI writes; the library adds id, status and history."""

    title: str = Field(min_length=2, max_length=120)
    rule: str = Field(min_length=2, max_length=2000, description="What to do (an instruction).")
    why: str = Field(default="", max_length=2000)
    strength: Strength = "should"
    topics: list[str] = Field(default_factory=list, max_length=6)
    scope: GuideScope = Field(default_factory=GuideScope)
    check: str | None = Field(default=None, description="Linked automated check (CHECKS), if any.")
    sources: list[GuideSource] = Field(default_factory=list, max_length=10)


class GuideEntry(GuideDraft):
    id: str
    status: GuideStatus
    origin: GuideOrigin
    version: int = 1
    created_at: str
    created_by: str = ""
    updated_at: str
    decided_by: str | None = None
    decided_at: str | None = None
    note: str | None = Field(default=None, max_length=1000, description="Why it was adopted / retired.")
    seed_version: int | None = None


# --- built-in baseline ---------------------------------------------------------------------------------

SEED_VERSION = 3
"""2: an entry applies by its first topic; LDO entries lead with "ldo". 3: G-PRT-02 checked by the fuse rule."""
_P = "일반 설계 관행 (앱 기본 지침)"


def _seed(id_: str, topics: list[str], strength: Strength, title: str, rule: str, why: str, check: str | None = None,
          parts: list[str] | None = None, keywords: list[str] | None = None, extra: str | None = None) -> dict:
    sources = [{"kind": "practice", "text": _P}]
    if check:
        sources.append({"kind": "app_rule", "text": f"앱 기술 검토 규칙: {CHECKS[check]} ({check})"})
    if extra:
        sources.append({"kind": "datasheet", "text": extra})
    return {"id": id_, "topics": topics, "strength": strength, "title": title, "rule": rule, "why": why, "check": check,
            "scope": {"parts": parts or [], "keywords": keywords or []}, "sources": sources}


SEED: list[dict] = [
    _seed("G-PWR-01", ["power"], "must", "전원 예산을 먼저 정한다",
          "전원 레일마다 전압과 최대 부하 전류를 정해 「전원 조건」에 적는다. 레귤레이터·다이오드·퓨즈·커넥터의 전류 정격은 "
          "최대 부하 전류보다 여유(보통 1.5~2배)를 둔다.",
          "부하 전류를 모르면 발열·정격 검토를 할 수 없고, 여유 없는 정격은 발열과 수명 문제로 이어진다.", "unknown_voltage",
          keywords=["전원", "power", "배터리", "battery", "어댑터"]),
    _seed("G-PWR-02", ["ldo", "power"], "must", "리니어 레귤레이터 발열을 계산한다",
          "손실 P = (Vin − Vout) × Iload 를 계산하고, 데이터시트의 열저항(θJA)으로 접합 온도가 최대값보다 충분히 낮은지 본다. "
          "전압 차이나 전류가 커서 발열이 크면 스위칭 레귤레이터를 검토한다.",
          "리니어 레귤레이터는 전압 차이를 모두 열로 버린다. 열 차단이나 수명 저하가 생긴다.", "reg_heat",
          extra="각 레귤레이터 데이터시트의 열 특성(Thermal Information) 표"),
    _seed("G-PWR-03", ["ldo", "power"], "must", "드롭아웃 전압을 확인한다",
          "최소 입력 전압 ≥ 출력 전압 + 드롭아웃 전압(데이터시트 최대값, 실제 부하 전류 조건)인지 확인한다.",
          "입력이 낮아지면 출력이 정격 전압을 유지하지 못한다(배터리 방전, 다이오드 강하 후 전압).", "reg_dropout"),
    _seed("G-PWR-04", ["power", "ldo", "dcdc"], "must", "레귤레이터 입출력 콘덴서는 데이터시트대로",
          "레귤레이터의 입력·출력 콘덴서 용량과 종류(세라믹/전해, ESR 조건)를 데이터시트 응용 회로대로 두고 핀 가까이 배치한다.",
          "일부 LDO는 출력 콘덴서의 ESR 범위를 벗어나면 발진한다. 스위칭 레귤레이터는 입력 콘덴서가 리플 전류를 감당해야 한다.",
          "decoupling", extra="각 레귤레이터 데이터시트의 응용 정보(입력·출력 콘덴서) 절"),
    _seed("G-PWR-05", ["power", "mcu", "general"], "must", "IC 전원 핀마다 디커플링 콘덴서",
          "IC의 전원 핀마다 100 nF 세라믹 콘덴서를 핀 가까이 두고, 레일마다 벌크 콘덴서(보통 1~10 µF 이상)를 하나 둔다.",
          "전원 핀의 순간 전류를 공급하고 잡음을 줄인다. 빠지면 오동작·리셋이 생길 수 있다.", "decoupling"),
    _seed("G-PWR-06", ["power"], "should", "콘덴서 전압 정격에 여유를 둔다",
          "콘덴서 전압 정격은 걸리는 최대 전압의 1.5~2배 이상으로 한다. 세라믹은 DC 바이어스로 용량이 줄어드는 것을 고려한다.",
          "정격에 가까우면 수명이 줄고, 세라믹은 실제 용량이 크게 떨어질 수 있다.", "cap_voltage"),
    _seed("G-PWR-07", ["power", "schematic"], "should", "전원 Net 이름에 전압을 넣는다",
          "전원 Net은 +5V, +3V3, +12V처럼 전압이 드러나게 이름 짓고, 이름에 전압이 없는 입력(VIN 등)은 「전원 조건」에 전압을 적는다.",
          "검토자가 바로 알 수 있고, 앱의 전압 기반 계산(정격, 발열)이 이 값을 쓴다.", "unknown_voltage"),
    _seed("G-PRT-01", ["protection", "power"], "should", "외부 전원 입력에 역극성 보호",
          "커넥터로 들어오는 외부 전원에는 직렬 쇼트키 다이오드(손실이 작음) 또는 P-MOSFET 역극성 보호를 둔다. "
          "다이오드는 역전압 정격 ≥ 입력 최대 전압, 전류 정격 ≥ 최대 부하 전류로 고른다.",
          "전원을 거꾸로 꽂으면 IC가 바로 손상될 수 있다.", "diode_reverse", keywords=["외부 전원", "어댑터", "dc jack", "배터리"]),
    _seed("G-PRT-02", ["protection", "power"], "should", "외부 전원 입력에 과전류 보호",
          "외부 전원 입력에 퓨즈나 PTC(폴리퓨즈)를 둔다. 유지 전류는 정상 최대 전류보다 크게, 차단 전류와 동작 시간은 배선·커넥터 "
          "정격 안에서 끊기도록 고른다.", "단락 때 배선·커넥터·기판 패턴이 타는 것을 막는다.", "fuse_rating"),
    _seed("G-PRT-03", ["protection", "connector"], "consider", "케이블로 나가는 선에는 서지·ESD 보호 검토",
          "케이블로 연결되는 전원·신호에는 TVS 다이오드를 검토한다. 동작 전압 ≥ 최대 정상 전압, 클램프 전압 < 보호할 부품의 절대 최대 정격.",
          "케이블은 정전기와 유도 서지가 들어오는 길이다."),
    _seed("G-MCU-01", ["mcu"], "must", "리셋 핀을 떠 있게 두지 않는다",
          "MCU 리셋 핀은 데이터시트 권장대로 풀업 저항(필요하면 콘덴서)을 둔다.",
          "떠 있는 리셋 핀은 잡음으로 리셋이 걸린다.", "floating_input"),
    _seed("G-MCU-02", ["mcu"], "must", "부트 모드 핀은 정해진 레벨로 고정",
          "BOOT0 같은 부트 모드 핀은 데이터시트대로 풀다운(또는 풀업)으로 고정한다.",
          "떠 있으면 전원을 켤 때마다 부팅 모드가 바뀔 수 있다.", "floating_input", parts=["STM32"]),
    _seed("G-MCU-03", ["mcu", "general"], "should", "쓰지 않는 입력 핀을 떠 있게 두지 않는다",
          "쓰지 않는 입력 핀은 데이터시트 권장대로 풀업·풀다운하거나 출력으로 설정한다. 회로도의 미연결 표시(X)는 일부러 비운 출력·NC 핀에만 쓴다.",
          "떠 있는 CMOS 입력은 전류를 더 쓰고 오동작한다.", "floating_input"),
    _seed("G-MCU-04", ["mcu", "analog"], "should", "아날로그 전원은 필터와 별도 디커플링",
          "AVCC·VDDA 같은 아날로그 전원은 데이터시트 권장대로 디지털 전원과 필터(페라이트 비드나 저항 + 콘덴서)로 연결하고 따로 디커플링한다.",
          "ADC 정확도가 디지털 잡음에 영향을 받는다.", "decoupling"),
    _seed("G-MCU-05", ["mcu", "connector"], "consider", "프로그래밍·디버그 커넥터를 둔다",
          "SWD·ISP·UART 부트로더 등 프로그램을 쓰고 디버그할 커넥터나 패드를 둔다.", "조립 후 펌웨어를 쓸 방법이 없으면 보드를 다시 만들어야 한다."),
    _seed("G-IF-01", ["interface"], "must", "I2C 버스마다 풀업 저항",
          "I2C의 SDA·SCL마다 전원으로 가는 풀업 저항을 버스에 하나씩 둔다(보통 2.2~10 kΩ, 버스 용량과 속도로 계산). "
          "여러 모듈의 풀업이 병렬로 겹쳐 너무 작아지지 않게 한다.", "I2C는 오픈 드레인이라 풀업이 없으면 동작하지 않는다.", "i2c_pullup",
          keywords=["I2C"]),
    _seed("G-IF-02", ["interface"], "consider", "CAN 버스 종단은 양 끝에만 120 Ω",
          "CAN 버스는 양 끝 노드에만 120 Ω 종단 저항을 둔다. 중간 노드에는 두지 않거나 점퍼로 끌 수 있게 한다.",
          "종단이 없거나 많으면 반사로 통신 오류가 난다.", parts=["SN65HVD", "MCP25", "TJA10"], keywords=["CAN"]),
    _seed("G-CLK-01", ["clock", "mcu"], "should", "크리스탈 부하 콘덴서를 계산한다",
          "크리스탈 양단 콘덴서는 C = 2 × (CL − Cstray)로 정한다(CL은 크리스탈 데이터시트, Cstray는 보통 2~5 pF). 크리스탈 가까이 둔다.",
          "부하 용량이 맞지 않으면 주파수가 틀어지거나 발진하지 않는다.", "crystal_caps"),
    _seed("G-LED-01", ["led"], "must", "LED 직렬 저항을 계산한다",
          "R = (공급 전압 − LED 순방향 전압) / 원하는 전류 로 정하고, 저항 전력 I²R이 정격 안인지 본다.",
          "저항이 없거나 작으면 LED와 구동 핀이 과전류로 손상된다.", "led_current", keywords=["LED"]),
    _seed("G-LED-02", ["led"], "must", "LED 방향을 확인한다",
          "애노드(A)는 전원·구동 쪽, 캐소드(K)는 GND·싱크 쪽에 연결한다.", "거꾸로 연결하면 켜지지 않는다.", "led_direction"),
    _seed("G-MOT-01", ["motor"], "must", "모터 드라이버 공급 전압에 여유를 둔다",
          "모터 전원(VM)의 최대 전압(어댑터 허용 오차, 모터 역기전력·되먹임 포함)이 드라이버의 VM 절대 최대 정격보다 충분히 낮게 한다.",
          "모터 감속·정지 때 전원이 순간적으로 올라갈 수 있다.", "motor_vm", keywords=["모터", "motor"]),
    _seed("G-MOT-02", ["motor", "power"], "should", "모터 전원에 벌크 콘덴서",
          "VM 핀 가까이 데이터시트 권장 용량 이상의 벌크 콘덴서(전압 정격 여유)와 세라믹 콘덴서를 둔다.",
          "모터 전류의 급변을 흡수해 전원 전압이 출렁이지 않게 한다.", "cap_voltage"),
    _seed("G-MOT-03", ["motor"], "must", "모터 드라이버 전류 제한을 계산한다",
          "전류 제한(ILIM 저항 등)을 데이터시트 식으로 계산해, 드라이버 정격 이하이면서 모터 기동·정지 전류에 맞게 정한다.",
          "제한이 너무 높으면 드라이버가 손상되고, 너무 낮으면 모터가 기동하지 못한다.", "motor_ilim"),
    _seed("G-MOT-04", ["motor"], "should", "모터 드라이버 발열을 계산한다",
          "손실 ≈ I² × (상단 + 하단 RDS(on))로 계산하고 패키지 열저항·PCB 방열 면적으로 온도를 확인한다.",
          "열 차단으로 모터가 멈출 수 있다.", "motor_heat"),
    _seed("G-MOT-05", ["motor", "mcu"], "should", "모터 제어 입력은 리셋 중에도 정해진 상태로",
          "MCU가 리셋 중이거나 연결이 끊겨도 모터가 돌지 않게 제어 입력(IN1/IN2 등)에 풀다운을 둔다(드라이버 내부 풀다운이 있는지 확인).",
          "MCU 핀은 리셋 중 입력(하이 임피던스)이라 모터가 의도치 않게 돌 수 있다.", "floating_input"),
    _seed("G-DCDC-01", ["dcdc", "power"], "should", "스위칭 레귤레이터는 레퍼런스 설계를 따른다",
          "스위칭 레귤레이터는 데이터시트의 응용 회로와 레이아웃 예(입력 콘덴서·인덕터·다이오드 위치, 피드백 배선)를 따른다.",
          "부품 배치와 배선이 효율, 잡음, 안정성을 좌우한다.", keywords=["벅", "buck", "부스트", "boost", "dc-dc", "스위칭"]),
    _seed("G-DCDC-02", ["dcdc"], "should", "인덕터 포화 전류에 여유를 둔다",
          "인덕터 포화 전류 ≥ 최대 부하 전류 + 리플 전류의 절반, 실효 전류 정격 ≥ 최대 부하 전류로 고른다.",
          "포화하면 전류가 급증해 레귤레이터가 손상되거나 보호 동작한다."),
    _seed("G-ANA-01", ["analog"], "should", "OP앰프 입력·출력 범위를 전원과 비교한다",
          "OP앰프의 입력 공통 모드 범위와 출력 스윙(레일 투 레일 여부)을 전원 전압과 신호 범위에 비교한다. 쓰지 않는 유닛은 데이터시트 권장대로 처리한다.",
          "범위를 벗어나면 출력이 포화되거나 왜곡된다.", parts=["Amplifier_Operational"]),
    _seed("G-SCH-01", ["schematic", "general"], "consider", "블록마다 짧은 주석과 신호 이름",
          "회로도 블록마다 기능과 핵심 사양을 짧게 적고(예: \"5V input, <100 mA\"), 중요한 신호 Net에는 의미 있는 이름을 붙인다.",
          "다른 사람이 회로도를 빨리 이해하고 검토할 수 있다."),
    _seed("G-SCH-02", ["schematic", "general"], "should", "부품·값을 고른 근거를 남긴다",
          "부품과 값마다 왜 골랐는지(요구사항, 계산, 데이터시트 절)를 설계 결정으로 남긴다.",
          "나중에 바꿀 때 무엇을 지켜야 하는지 알 수 있다."),
]


# --- which guides apply ----------------------------------------------------------------------------------

_REQ_TOPICS: list[tuple[str, tuple[str, ...]]] = [
    # Acronyms in capitals match only in capitals (see has_word).
    ("power", ("전원", "power", "배터리", "battery", "어댑터", "adapter", "레귤레이터", "regulator", "LDO", "벅", "buck")),
    ("ldo", ("LDO", "리니어", "linear", "7805", "1117", "레귤레이터")),
    ("dcdc", ("벅", "buck", "boost", "부스트", "DC-DC", "DCDC", "스위칭")),
    ("protection", ("보호", "역전압", "역극성", "서지", "ESD", "퓨즈", "fuse", "TVS", "외부 전원")),
    ("mcu", ("MCU", "마이컴", "마이크로컨트롤러", "microcontroller", "STM32", "ATmega", "ESP32", "RP2040", "펌웨어")),
    ("interface", ("I2C", "SPI", "UART", "CAN", "USB", "RS485", "RS-485", "통신")),
    ("motor", ("모터", "motor")),
    ("led", ("LED", "표시등", "램프")),
    ("clock", ("크리스탈", "crystal", "클럭", "clock", "발진")),
    ("analog", ("OP앰프", "opamp", "op-amp", "센서", "sensor", "ADC", "증폭")),
]


def has_word(text: str, word: str) -> bool:
    """``word`` in ``text``: Latin words as whole words ("led" is not in "controlled", "can" not in "scan"),
    other scripts as a substring (Korean particles attach to the word: "모터를")."""
    raw = word.strip()
    if not raw:
        return False
    if re.fullmatch(r"[A-Z0-9][A-Z0-9 .\-]*", raw) and re.search(r"[A-Z]", raw):
        # An acronym (CAN, I2C, LED): capitals only, so the English word "can" does not count.
        return re.search(rf"(?<![A-Za-z0-9]){re.escape(raw)}(?![A-Za-z0-9])", text) is not None
    w = raw.lower()
    if re.fullmatch(r"[a-z0-9][a-z0-9 .\-]*", w):
        return re.search(rf"(?<![a-z0-9]){re.escape(w)}(?![a-z0-9])", text.lower()) is not None
    return w in text.lower()


def topics_of_text(text: str) -> set[str]:
    return {topic for topic, words in _REQ_TOPICS if any(has_word(text or "", w) for w in words)}


def topics_of_circuit(circuit: CircuitIR | None) -> set[str]:
    """Topics a circuit touches, from its parts and net names."""
    found = {"general", "schematic"}
    if circuit is None:
        return found
    for comp in circuit.components:
        lib = comp.symbol_id.split(":")[0]
        cat = category_of(comp.symbol_id, comp.reference)
        value = f"{comp.value} {comp.manufacturer_part_number or ''}".upper()
        if lib == "Regulator_Linear" or re.search(r"(L?M?78\d\d|1117)", value):
            found |= {"power", "ldo"}
        if lib == "Regulator_Switching" or cat == "inductor":
            found |= {"power", "dcdc"}
        if lib.startswith("MCU_"):
            found.add("mcu")
        if lib.startswith("Interface"):
            found.add("interface")
        if lib == "Driver_Motor":
            found |= {"motor", "power"}
        if cat == "led":
            found.add("led")
        if cat == "crystal" or lib.startswith("Oscillator"):
            found.add("clock")
        if lib.startswith("Amplifier") or lib.startswith("Sensor"):
            found.add("analog")
        if cat == "connector":
            found.add("connector")
        if cat in ("fuse",) or (cat == "diode" and "TVS" in comp.symbol_id.upper()):
            found.add("protection")
    connectors = {c.id for c in circuit.components if category_of(c.symbol_id, c.reference) == "connector"}
    for net in circuit.nets:
        if re.search(r"(^|_)(SDA|SCL|CAN|TX|RX|MOSI|MISO|USB)", net.name, re.I):
            found.add("interface")
        supply = net.name.startswith("+") or bool(net.voltage) or bool(re.search(r"VIN|VBUS|VCC|PWR", net.name, re.I))
        if supply:
            found.add("power")
            if any(n.component_id in connectors for n in net.nodes):
                found.add("protection")  # power comes in (or goes out) through a connector
    return found


def _part_hit(entry: GuideEntry, components: Iterable[Component]) -> bool:
    words = [w.upper() for w in entry.scope.parts if w.strip()]
    if not words:
        return False
    for comp in components:
        text = f"{comp.value} {comp.manufacturer_part_number or ''} {comp.symbol_id}".upper()
        if any(w in text for w in words):
            return True
    return False


def relevant(entries: Iterable[GuideEntry], *, circuit: CircuitIR | None = None, text: str = "",
             statuses: tuple[str, ...] = ("adopted",), text_topics: bool | None = None) -> list[GuideEntry]:
    """Entries that apply to a circuit and/or a requirements text, strongest first.

    An entry applies by its first (main) topic; the others are tags (a motor entry tagged "power" is not a power
    entry). The text's words count as topics only when ``text_topics`` (default: when there is no circuit yet, as in
    a new design); with a circuit they only match an entry's own keywords and part names ("host MCU" in the text does
    not mean this board has an MCU)."""
    use_text = circuit is None if text_topics is None else text_topics
    topics = topics_of_circuit(circuit) | (topics_of_text(text) if use_text else set())
    low = (text or "").lower()
    out = []
    for e in entries:
        if e.status not in statuses:
            continue
        hit = bool(e.topics) and e.topics[0] in topics
        if e.scope.parts:
            # A part-specific entry needs its part (unless the requirements name one of its words).
            # Part names are prefixes of real part numbers: "SN65HVD" names SN65HVD230.
            hit = _part_hit(e, circuit.components if circuit else []) or any(w.lower() in low for w in e.scope.parts)
        if not hit and e.scope.keywords and any(has_word(text or "", k) for k in e.scope.keywords):
            hit = True
        if hit:
            out.append(e)
    order = {"must": 0, "should": 1, "consider": 2}
    return sorted(out, key=lambda e: (order[e.strength], e.id))


def prompt_text(entries: list[GuideEntry], limit_chars: int = 6000) -> str:
    """The adopted guides as instructions for an AI prompt (id, strength, rule, why), within a size limit."""
    lines, size = [], 0
    for e in entries:
        line = f"- [{e.id}] ({STRENGTH_LABEL[e.strength]}) {e.title}: {e.rule}" + (f" — 이유: {e.why}" if e.why else "")
        if size + len(line) > limit_chars:
            lines.append(f"- … 외 {len(entries) - len(lines)}개 (설계 가이드 화면 참고)")
            break
        lines.append(line)
        size += len(line)
    return "\n".join(lines)


# --- checklist -----------------------------------------------------------------------------------------

def checklist(entries: list[GuideEntry], findings: list[dict], floating: list[dict],
              exceptions: dict[str, dict] | None = None) -> list[dict]:
    """Each guide with what the app could tell automatically: ``fail`` / ``warn`` (the linked check found a
    problem), ``ok`` (the linked check ran and found none), ``manual`` (a person has to look), or ``exception``
    (a design decision of the circuit deliberately does not follow it; its reason is the decision's reason)."""
    by_rule: dict[str, list[dict]] = {}
    for f in findings:
        by_rule.setdefault(f["rule"], []).append(f)
    exceptions = exceptions or {}
    rows = []
    for e in entries:
        status, notes = "manual", []
        waiver = exceptions.get(e.id)
        if waiver is not None:
            rows.append({"id": e.id, "title": e.title, "rule": e.rule, "strength": e.strength, "topics": e.topics,
                         "check": e.check, "status": "exception", "origin": e.origin,
                         "notes": [f"설계 결정 「{waiver['topic']}」: {waiver['choice']} — {waiver['reason']}"],
                         "decision": waiver["id"]})
            continue
        if e.check == "floating_input":
            if floating:
                status = "warn"
                notes = [f"{f['reference']}.{f['pin']} {f.get('pin_name') or ''}: {f['problem']}" for f in floating][:6]
            else:
                status, notes = "ok", ["떠 있는 입력 핀 없음"]
        elif e.check and e.check in by_rule:
            hits = by_rule[e.check]
            worst = min(hits, key=lambda f: {"fail": 0, "warn": 1, "info": 2, "ok": 3}[f["severity"]])["severity"]
            status = {"fail": "fail", "warn": "warn"}.get(worst, "ok")
            notes = [f["title"] for f in hits if f["severity"] in ("fail", "warn")][:6] or [hits[0]["title"]]
        rows.append({"id": e.id, "title": e.title, "rule": e.rule, "strength": e.strength, "topics": e.topics,
                     "check": e.check, "status": status, "notes": notes, "origin": e.origin})
    return rows


# --- document --------------------------------------------------------------------------------------------

def to_markdown(entries: list[GuideEntry], *, title: str = "회로 설계 가이드", generated_at: str = "") -> str:
    """The adopted guide as a document (by topic), then the open candidates."""
    adopted = [e for e in entries if e.status == "adopted"]
    candidates = [e for e in entries if e.status == "candidate"]
    out = [f"# {title}", ""]
    if generated_at:
        out += [f"- 만든 때: {generated_at}"]
    out += [f"- 채택된 지침 {len(adopted)}개, 검토 대기 후보 {len(candidates)}개",
            "- 「필수」는 지키지 않으면 결함, 「권장」은 이유가 없으면 따름, 「검토」는 상황에 따라 판단.", ""]

    def block(e: GuideEntry) -> list[str]:
        lines = [f"### {e.id} {e.title} ({STRENGTH_LABEL[e.strength]})", "", e.rule, ""]
        if e.why:
            lines += [f"- 이유: {e.why}"]
        if e.check:
            lines += [f"- 자동 검사: {CHECKS.get(e.check, e.check)}"]
        for s in e.sources:
            lines += [f"- 출처: {s.text}" + (f" ({s.url})" if s.url else "")]
        lines += [f"- 상태: v{e.version}, {e.decided_at or e.created_at} {e.decided_by or ''}".rstrip(), ""]
        return lines

    for topic, label in TOPICS.items():
        items = [e for e in adopted if (e.topics or ["general"])[0] == topic]
        if items:
            out += [f"## {label}", ""]
            for e in items:
                out += block(e)
    if candidates:
        out += ["## 검토 대기 후보 (아직 쓰지 않음)", ""]
        for e in candidates:
            out += block(e)
    return "\n".join(out).rstrip() + "\n"


# --- importing a guide document ----------------------------------------------------------------------------

_STRENGTH_WORDS = {"필수": "must", "must": "must", "권장": "should", "should": "should", "검토": "consider",
                   "consider": "consider", "참고": "consider"}
_TOPIC_BY_LABEL = {**{v: k for k, v in TOPICS.items()}, **{k: k for k in TOPICS}}
_CHECK_BY_LABEL = {**{v: k for k, v in CHECKS.items()}, **{k: k for k in CHECKS}}


def _topic_of(label: str) -> str | None:
    label = label.strip()
    return _TOPIC_BY_LABEL.get(label) or next((k for lab, k in _TOPIC_BY_LABEL.items() if lab and lab in label), None)


def parse_guide_markdown(text: str) -> tuple[list[GuideDraft], list[str]]:
    """A guide written as Markdown: the app's own export (``## 분야`` / ``### ID 제목 (강도)`` / rule / ``- 이유:``),
    or a plain list under ``## 분야`` headings: ``- [필수] 제목: 할 일``. Returns (drafts, warnings)."""
    drafts: list[GuideDraft] = []
    warnings: list[str] = []
    topic = "general"
    cur: dict | None = None

    def flush():
        nonlocal cur
        if cur and cur.get("title") and cur.get("rule"):
            try:
                drafts.append(GuideDraft(**cur))
            except ValueError as exc:
                warnings.append(f"「{cur.get('title')}」: {exc.errors()[0]['msg'] if hasattr(exc, 'errors') else exc}")
        cur = None

    for line in text.splitlines():
        raw = line.rstrip()
        if raw.startswith("## "):
            flush()
            label = raw[3:].strip()
            topic = _topic_of(label) or topic
            if label.startswith("검토 대기 후보"):
                topic = "general"
            continue
        m = re.match(r"^###\s+(?:(G-[A-Z0-9-]+)\s+)?(.+?)\s*(?:\((필수|권장|검토)\))?\s*$", raw)
        if m:
            flush()
            cur = {"title": m.group(2).strip()[:120], "rule": "", "why": "", "strength": _STRENGTH_WORDS.get(m.group(3) or "", "should"),
                   "topics": [topic], "sources": [{"kind": "practice", "text": "가져온 설계 가이드 문서"}]}
            continue
        m = re.match(r"^[-*]\s*\[(필수|권장|검토|참고|must|should|consider)\]\s*([^:：]+)[:：]\s*(.+)$", raw, re.I)
        if m:
            flush()
            cur = {"title": m.group(2).strip()[:120], "rule": m.group(3).strip()[:2000], "why": "",
                   "strength": _STRENGTH_WORDS[m.group(1).lower() if m.group(1).isascii() else m.group(1)],
                   "topics": [topic], "sources": [{"kind": "practice", "text": "가져온 설계 가이드 문서"}]}
            flush()
            continue
        if cur is None:
            continue
        item = re.match(r"^-\s*(이유|자동 검사|출처|상태)\s*[:：]\s*(.*)$", raw)
        if item:
            key, value = item.group(1), item.group(2).strip()
            if key == "이유":
                cur["why"] = value[:2000]
            elif key == "자동 검사":
                cur["check"] = _CHECK_BY_LABEL.get(value)
            elif key == "출처" and value:
                cur["sources"] = [s for s in cur["sources"] if s["text"] != "가져온 설계 가이드 문서"] + \
                                 [{"kind": "practice", "text": value[:500]}]
            continue
        if raw.strip() and not raw.startswith(("#", ">", "|")):
            cur["rule"] = (cur["rule"] + " " + raw.strip()).strip()[:2000]
    flush()
    return drafts, warnings


def parse_guide_csv(text: str) -> tuple[list[GuideDraft], list[str]]:
    """CSV with a header: 제목/title, 지침/rule, 이유/why, 강도/strength, 분야/topics, 자동 검사/check, 출처/source."""
    import csv
    import io

    names = {"제목": "title", "title": "title", "지침": "rule", "할 일": "rule", "rule": "rule", "이유": "why",
             "why": "why", "강도": "strength", "strength": "strength", "분야": "topics", "topics": "topics",
             "topic": "topics", "자동 검사": "check", "check": "check", "출처": "source", "source": "source"}
    reader = csv.DictReader(io.StringIO(text.lstrip("\ufeff")))
    drafts, warnings = [], []
    for n, row in enumerate(reader, start=2):
        rec = {names.get((k or "").strip().lower(), names.get((k or "").strip(), "")): (v or "").strip()
               for k, v in row.items()}
        if not rec.get("title") or not rec.get("rule"):
            warnings.append(f"{n}행: 제목과 지침이 필요합니다.")
            continue
        topics = [t for t in (_topic_of(x) for x in re.split(r"[,;/]", rec.get("topics", ""))) if t] or ["general"]
        try:
            drafts.append(GuideDraft(title=rec["title"][:120], rule=rec["rule"][:2000], why=rec.get("why", "")[:2000],
                                     strength=_STRENGTH_WORDS.get(rec.get("strength", "").lower(),
                                                                  _STRENGTH_WORDS.get(rec.get("strength", ""), "should")),
                                     topics=list(dict.fromkeys(topics))[:6], check=_CHECK_BY_LABEL.get(rec.get("check", "")),
                                     sources=[{"kind": "practice", "text": rec.get("source") or "가져온 설계 가이드 CSV"}]))
        except ValueError as exc:
            warnings.append(f"{n}행: {exc}")
    return drafts, warnings
