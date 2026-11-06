"""SCU (System Control Unit) main board for the next-generation CT - concept schematic v0.1.

Parts go through the app's part resolver (KiCad symbol + footprint from the package) and build_circuit (pin checks,
every pin connected or marked unused). The AI step is skipped: the board has ~200 parts, over the AI design path's
60-part limit, so this file plays the designer.

It reads the KiCad 10.0.8 symbols copied into tests/fixtures/kicad_symbols, so the result is the committed circuit.json.

usage: uv run python examples/scu-main-board/design.py [out-dir]   (default: this folder)
"""

import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from ai_engine.design import build_circuit  # noqa: E402
from backend.design_resolver import PartResolver, symbol_index  # noqa: E402
from kicad_adapter.symbols import SymbolLibrary  # noqa: E402

LIBDIR = ROOT / "tests" / "fixtures" / "kicad_symbols"
LIB = SymbolLibrary([LIBDIR])

PAGES = ["Power", "MCU", "Memory_Supervisor", "Ethernet", "Fieldbus_Sync", "Isolated_IO", "Safety_Interface"]
parts: list[dict] = []
page_of: dict[str, str] = {}
nets: dict[str, list[str]] = defaultdict(list)
no_connect: list[str] = []
rails: list[dict] = []
notes: list[dict] = []
count: dict[str, int] = defaultdict(int)
page = "Power"


class Part:
    def __init__(self, ref: str, symbol: str | None):
        self.ref, self.symbol = ref, symbol

    def __getitem__(self, pin) -> str:
        pin = str(pin)
        if self.symbol and not any(p.number == pin for p in LIB.get(self.symbol).pins):
            names = {p.name: p.number for p in LIB.get(self.symbol).pins}
            if pin in names:
                pin = names[pin]
            else:
                raise KeyError(f"{self.ref}: no pin {pin}")
        return f"{self.ref}.{pin}"


def P(prefix: str, category: str, value: str, role: str, *, symbol: str | None = None, **kw) -> Part:
    count[(prefix, page)] += 1  # page-based numbers: R101.. on page 1, U201.. on page 2
    ref = f"{prefix}{(PAGES.index(page) + 1) * 100 + count[(prefix, page)]}"
    d = {"ref": ref, "category": category, "value": value, "role": role, **kw}
    if symbol:
        d["kicad_symbol"] = symbol
    parts.append(d)
    page_of[ref] = page
    from backend.design_resolver import GENERIC_SYMBOLS
    return Part(ref, symbol or GENERIC_SYMBOLS.get(category))


def N(name: str, *pins: str) -> None:
    nets[name].extend(pins)


def R(value, a, b, role, package="0603", **kw):
    r = P("R", "resistor", value, role, package=package, **kw)
    N(a, r[1])
    N(b, r[2])
    return r


def C(value, a, b, role, package="0603", **kw):
    c = P("C", "capacitor", value, role, package=package, **kw)
    N(a, c[1])
    N(b, c[2])
    return c


def note(text, refs=(), net=""):
    notes.append({"text": text, "refs": [r.ref if isinstance(r, Part) else r for r in refs], "net": net})


# =============================================================================================== Power
page = "Power"
J1 = P("J", "connector", "24V_IN", "24 V 입력 (PDB에서)", pin_count=2, package="5.0mm 단자대",
       basis="requirement", request="PWR-101 24 VDC: 제어, 센서, 릴레이, 보조 구동")
N("24V_IN", J1[1]); N("GND", J1[2])
F1 = P("F", "fuse", "2A T", "입력 퓨즈 (지연형)", package="1812", reason="보드 최대 입력 약 1.2 A(3.3 V 계통 0.2 A + DO 1 A)의 1.7배",
       basis="requirement", request="입력 과전압·과전류 보호(PWR-004)")
N("24V_IN", F1[1]); N("24V_F", F1[2])
D1 = P("D", "diode", "B360", "역극성 보호 (직렬 쇼트키)", symbol="Diode:B360", mpn="B360-13-F", manufacturer="Diodes Incorporated",
       reason="60 V 3 A 쇼트키: 역접속 −30 V 차단, 손실 약 0.5 V × 1.2 A = 0.6 W(SMC)", basis="requirement",
       request="입력 역극성 및 단락 보호")
N("24V_F", D1["A"]); N("24V_P", D1["K"])
D2 = P("D", "diode", "SMAJ33A", "입력 TVS", symbol="Diode:SMAJ33A", mpn="SMAJ33A", reason="VRWM 33 V(입력 최대 30 V 위), 클램프 약 53 V < TPS54360 최대 65 V",
       basis="requirement", request="서지 및 과도전압 보호(PWR-005)")
N("24V_P", D2[1]); N("GND", D2[2])
CB = P("C", "capacitor_polarized", "47uF/50V", "입력 벌크 (전해)", package="8x10", reason="케이블 인덕턴스와 공진 감쇠, 부하 과도")
N("24V_P", CB[1]); N("GND", CB[2])
C("2.2uF/50V", "24V_P", "GND", "입력 세라믹", package="1210")
RSH = R("0.1", "24V_P", "+24V", "입력 전류 검출 션트 (1 %, 1 W)", package="2512", calc="0.1 Ω × 1.2 A = 120 mV, 144 mW")
U_INA = P("U", "ic", "INA240A1", "입력 전류 감시 앰프 (×20)", symbol="Amplifier_Current:INA240A1D", mpn="INA240A1DR",
          manufacturer="Texas Instruments", reason="공통 모드 −4~80 V로 24 V 계통 직접 측정, 이득 20: 1 A → 2.0 V",
          basis="requirement", request="각 전원 Rail의 전압·전류·온도 감시")
N("24V_P", U_INA["+"]); N("+24V", U_INA["-"])
N("GND", U_INA[2], U_INA[4], U_INA["REF1"], U_INA["REF2"]); N("+3V3", U_INA["V+"]); N("I24_OUT", U_INA[5])
C("100nF/16V", "+3V3", "GND", "INA240 디커플링")
R("1k", "I24_OUT", "I24_MON", "ADC 필터 R")
C("100nF/16V", "I24_MON", "GND", "ADC 필터 C (fc ≈ 1.6 kHz)")
F2 = P("F", "fuse", "PTC 1.5A", "DO 부하 전원 (리셋형)", package="1812", reason="DO 4채널 합계 1 A: 유지 1.5 A로 고온 감소 여유")
N("+24V", F2[1]); N("+24V_DO", F2[2])

U_BUCK = P("U", "ic", "TPS54360", "24 V → 3.3 V 강압 컨버터", symbol="Regulator_Switching:TPS54360DDA", mpn="TPS54360DDAR",
           manufacturer="Texas Instruments", reason="입력 4.5~60 V(서지 여유), 3.5 A, 비동기 + B360 캐치 다이오드",
           basis="requirement", request="PWR-105 3.3 VDC: MCU, FPGA I/O")
N("+24V", U_BUCK["VIN"]); N("GND", U_BUCK["GND"], U_BUCK["GNDPAD"])
C("2.2uF/50V", "+24V", "GND", "벅 입력 세라믹", package="1210")
C("2.2uF/50V", "+24V", "GND", "벅 입력 세라믹", package="1210")
C("100nF/50V", "+24V", "GND", "벅 입력 고주파")
R("510k", "+24V", "EN_BUCK", "UVLO 분압 상단", calc="시작 약 16 V / 정지 약 14 V (데이터시트 식으로 재확인)")
R("39.2k", "EN_BUCK", "GND", "UVLO 분압 하단")
N("EN_BUCK", U_BUCK["EN"])
R("196k", "RT_BUCK", "GND", "스위칭 주파수 설정", calc="RT = 101756 / f^1.008 → 약 500 kHz")
N("RT_BUCK", U_BUCK["RT/CLK"])
R("31.6k", "+3V3", "FB_BUCK", "출력 분압 상단", calc="0.8 V × (1 + 31.6k / 10k) = 3.33 V")
R("10k", "FB_BUCK", "GND", "출력 분압 하단")
N("FB_BUCK", U_BUCK["FB"])
R("22k", "COMP_BUCK", "COMP_RC", "보상 R (데이터시트/WEBENCH로 확정)")
C("3.3nF/50V", "COMP_RC", "GND", "보상 C")
C("47pF/50V", "COMP_BUCK", "GND", "보상 고주파 C")
N("COMP_BUCK", U_BUCK["COMP"])
C("100nF/50V", "BOOT_BUCK", "SW_BUCK", "부트스트랩")
N("BOOT_BUCK", U_BUCK["BOOT"]); N("SW_BUCK", U_BUCK["SW"])
L1 = P("L", "inductor", "10uH", "벅 인덕터", package="SRN6045TA", mpn="SRN6045TA-100M", manufacturer="Bourns",
       calc="ΔI = (24 − 3.3) × 3.3 / (24 × 10 µH × 500 kHz) = 0.57 A")
N("SW_BUCK", L1[1]); N("+3V3", L1[2])
D3 = P("D", "diode", "B360", "벅 캐치 다이오드", symbol="Diode:B360", mpn="B360-13-F", manufacturer="Diodes Incorporated")
N("SW_BUCK", D3["K"]); N("GND", D3["A"])
C("47uF/6.3V", "+3V3", "GND", "벅 출력", package="1210")
C("47uF/6.3V", "+3V3", "GND", "벅 출력", package="1210")
C("100nF/16V", "+3V3", "GND", "벅 출력 고주파")
FB1 = P("FB", "inductor", "600R@100MHz", "아날로그 전원 분리 (VDDA)", symbol="Device:FerriteBead", package="0603")
N("+3V3", FB1[1]); N("+3V3A", FB1[2])
C("1uF/16V", "+3V3A", "GND", "VDDA 벌크")
U_REF = P("U", "ic", "REF3030", "ADC 기준전압 3.0 V", symbol="Reference_Voltage:REF3030", mpn="REF3030AIDBZR",
          manufacturer="Texas Instruments", reason="0.2 % 초기 오차, 50 ppm/°C: ADC 전압·전류·온도 감시 정확도",
          basis="requirement", request="PWR-107 별도 저노이즈 전원: 기준전압")
N("+3V3A", U_REF["IN"]); N("VREF_3V0", U_REF["OUT"]); N("GND", U_REF["GND"])
C("100nF/16V", "+3V3A", "GND", "REF 입력")
C("1uF/16V", "VREF_3V0", "GND", "REF 출력")
R("1k", "+3V3", "PWR_LED", "전원 LED 저항")
DL = P("D", "led", "RED", "3.3 V 전원 LED", package="0603")
N("PWR_LED", DL["A"]); N("GND", DL["K"])
R("100k", "+24V", "V24_MON", "24 V 감시 분압 상단", calc="30 V × 10k / 110k = 2.73 V < VREF 3.0 V")
R("10k", "V24_MON", "GND", "24 V 감시 분압 하단")
C("100nF/16V", "V24_MON", "GND", "24 V 감시 필터")
R("100k", "+24V_DO", "V24DO_MON", "DO 전원 감시 분압 상단")
R("10k", "V24DO_MON", "GND", "DO 전원 감시 분압 하단")
C("100nF/16V", "V24DO_MON", "GND", "DO 전원 감시 필터")
R("10k", "+3V3", "V3V3_MON", "3.3 V 감시 분압 상단")
R("10k", "V3V3_MON", "GND", "3.3 V 감시 분압 하단")
C("100nF/16V", "V3V3_MON", "GND", "3.3 V 감시 필터")
note("24V in: fuse, reverse diode, TVS", [J1, F1, D1])
note("TPS54360 buck 24V to 3.3V, 500kHz", [U_BUCK])
note("Input current: 0.1R x20 = 2.0V/A", [U_INA])
note("ADC ref 3.000V for monitors", [U_REF])

# =============================================================================================== MCU
page = "MCU"
U1 = P("U", "ic", "STM32H743ZIT6", "메인 MCU (Cortex-M7 480 MHz)", symbol="MCU_ST_STM32H7:STM32H743ZITx", mpn="STM32H743ZIT6",
       manufacturer="STMicroelectronics", reason="FDCAN 2개(CAN FD), Ethernet MAC(RMII), 고분해능 타이머, 16-bit ADC, 2 MB Flash",
       basis="requirement", request="SCU-001 메인 프로세서: 산업용 MCU 또는 MPU/SoC")
sym = LIB.get(U1.symbol)
for p in sym.pins:
    if p.name == "VDD":
        N("+3V3", U1[p.number])
N("+3V3", U1["VDD33_USB"], U1["VBAT"], U1["PDR_ON"])
N("GND", U1[16], U1["VSSA"])
N("+3V3A", U1["VDDA"]); N("VREF_3V0", U1["VREF+"])
N("VCAP1", U1[71]); N("VCAP2", U1[106])
for _ in range(11):
    C("100nF/16V", "+3V3", "GND", "MCU VDD 디커플링 (핀마다 1개)")
C("4.7uF/10V", "+3V3", "GND", "MCU 벌크", package="0805")
C("2.2uF/10V", "VCAP1", "GND", "VCAP1 (내부 LDO, ESR < 100 mΩ)", package="0805")
C("2.2uF/10V", "VCAP2", "GND", "VCAP2 (내부 LDO, ESR < 100 mΩ)", package="0805")
C("100nF/16V", "+3V3A", "GND", "VDDA 디커플링")
C("100nF/16V", "VREF_3V0", "GND", "VREF+ 디커플링")
N("NRST", U1["NRST"])
C("100nF/16V", "NRST", "GND", "NRST 필터")
R("10k", "BOOT0", "GND", "BOOT0 풀다운 (Flash 부팅)")
N("BOOT0", U1["BOOT0"])
Y1 = P("Y", "crystal", "25MHz", "HSE 크리스탈", symbol="Device:Crystal_GND24", package="3225", reason="25 MHz: PLL로 480 MHz",
       calc="CL 10 pF 품: 2 × (10 − 3) pF ≈ 12~15 pF")
N("HSE_IN", Y1[1]); N("HSE_OUT", Y1[3]); N("GND", Y1[2], Y1[4])
C("12pF/50V", "HSE_IN", "GND", "HSE 부하 C")
C("12pF/50V", "HSE_OUT", "GND", "HSE 부하 C")
N("HSE_IN", U1["PH0"]); N("HSE_OUT", U1["PH1"])
Y2 = P("Y", "crystal", "32.768kHz", "LSE 크리스탈 (RTC)", package="2012", calc="CL 6 pF 품: 2 × (6 − 2) pF ≈ 8 pF")
N("LSE_IN", Y2[1]); N("LSE_OUT", Y2[2])
C("8.2pF/50V", "LSE_IN", "GND", "LSE 부하 C")
C("8.2pF/50V", "LSE_OUT", "GND", "LSE 부하 C")
N("LSE_IN", U1["PC14"]); N("LSE_OUT", U1["PC15"])
J_SWD = P("J", "connector", "SWD", "SWD 디버그 (Cortex 10핀)", symbol="Connector:Conn_ARM_JTAG_SWD_10", package="1.27mm 2x05",
          basis="requirement", request="SCU-012 FW 업데이트·복구, 유지보수 Debug")
N("+3V3", J_SWD["VTref"]); N("SWDIO", J_SWD[2]); N("SWCLK", J_SWD[4]); N("SWO", J_SWD[6])
N("GND", J_SWD[3], J_SWD[5], J_SWD[9]); N("NRST", J_SWD[10])
no_connect += [J_SWD[7], J_SWD[8]]
J_UART = P("J", "connector", "CONSOLE", "디버그 UART", pin_count=3, package="2.54mm 헤더")
N("UART_TX", J_UART[1]); N("UART_RX", J_UART[2]); N("GND", J_UART[3])
R("1k", "LED_HB", "LED_HB_A", "Heartbeat LED 저항")
D_HB = P("D", "led", "YELLOW", "Heartbeat LED (3.3 V 구동: Vf 낮은 색)", package="0603")
N("LED_HB_A", D_HB["A"]); N("GND", D_HB["K"])
R("1k", "LED_FAULT", "LED_FAULT_A", "Fault LED 저항")
D_FT = P("D", "led", "RED", "Fault LED", package="0603")
N("LED_FAULT_A", D_FT["A"]); N("GND", D_FT["K"])
note("STM32H743 480MHz, internal LDO", [U1])
note("SWD 10-pin / UART console", [J_SWD, J_UART])

# MCU pin assignment (alternate functions per STM32H743 datasheet; to be confirmed in STM32CubeMX)
GPIO = {
    "PA1": "ETH_REF_CLK", "PA2": "ETH_MDIO", "PA3": "V24DO_MON", "PA4": "V3V3_MON", "PA6": "I24_MON", "PA7": "ETH_CRS_DV",
    "PA13": "SWDIO", "PA14": "SWCLK", "PA15": "SYNC_IN",
    "PB0": "NTC1", "PB1": "NTC2", "PB3": "SWO", "PB5": "FDCAN2_RX", "PB6": "FDCAN2_TX", "PB10": "SPI2_SCK",
    "PB12": "FRAM_CS", "PB13": "ETH_TXD1", "PB14": "SPI2_MISO", "PB15": "SPI2_MOSI",
    "PC0": "V24_MON", "PC1": "ETH_MDC", "PC4": "ETH_RXD0", "PC5": "ETH_RXD1", "PC6": "EXPO_FB",
    "PD0": "FDCAN1_RX", "PD1": "FDCAN1_TX", "PD8": "UART_TX", "PD9": "UART_RX", "PD10": "FLASH_CS",
    "PE0": "LED_HB", "PE1": "LED_FAULT", "PE2": "DI1", "PE3": "DI2", "PE4": "DI3", "PE5": "DI4", "PE9": "TRIG1", "PE11": "TRIG2",
    "PF0": "DO1", "PF1": "DO2", "PF2": "DO3", "PF3": "DO4", "PF4": "SCU_ALIVE", "PF5": "XRAY_REQ",
    "PG0": "DO1_FB", "PG1": "DO2_FB", "PG2": "DO3_FB", "PG3": "DO4_FB", "PG4": "WDT_WDI", "PG5": "PHY_RST_N",
    "PG6": "SAFETY_OK", "PG7": "XRAY_PERMIT", "PG11": "ETH_TX_EN", "PG13": "ETH_TXD0",
}
for pin_name, net in GPIO.items():
    N(net, U1[pin_name])

# =============================================================================================== Memory / Supervisor
page = "Memory_Supervisor"
U_FRAM = P("U", "ic", "MB85RS2MT", "FRAM 2 Mbit (설정·진단·이력)", symbol="Memory_NVRAM:MB85RS2MT", package="SOIC-8",
           mpn="MB85RS2MTAPNF-G-BDERE1", manufacturer="RAMXEED (Fujitsu)", reason="10^13회 쓰기, 쓰기 지연 없음: 정전 직전 Fault 기록",
           basis="requirement", request="SCU-003 비휘발성 메모리: 설정·진단·이력 저장")
N("FRAM_CS", U_FRAM[1]); N("SPI2_MISO", U_FRAM["SO"]); N("SPI2_MOSI", U_FRAM["SI"]); N("SPI2_SCK", U_FRAM["SCK"])
N("+3V3", U_FRAM["VDD"], U_FRAM[3], U_FRAM[7]); N("GND", U_FRAM["GND"])
C("100nF/16V", "+3V3", "GND", "FRAM 디커플링")
R("10k", "+3V3", "FRAM_CS", "CS 풀업 (리셋 중 비선택)")
U_FLASH = P("U", "ic", "W25Q128JVS", "SPI NOR 128 Mbit (FW 이미지 A/B, 로그)", symbol="Memory_Flash:W25Q128JVS",
            mpn="W25Q128JVSIQ", manufacturer="Winbond", reason="인증된 업데이트용 두 번째 이미지와 복구 이미지 저장",
            basis="requirement", request="SCU-012 인증된 업데이트 및 복구 기능")
N("FLASH_CS", U_FLASH[1]); N("SPI2_MISO", U_FLASH[2]); N("SPI2_MOSI", U_FLASH[5]); N("SPI2_SCK", U_FLASH["CLK"])
N("+3V3", U_FLASH["VCC"]); N("GND", U_FLASH["GND"]); N("FLASH_WP", U_FLASH[3]); N("FLASH_HOLD", U_FLASH[7])
R("10k", "+3V3", "FLASH_WP", "WP 풀업 (나중에 QSPI로 쓸 수 있게)")
R("10k", "+3V3", "FLASH_HOLD", "HOLD 풀업")
C("100nF/16V", "+3V3", "GND", "Flash 디커플링")
R("10k", "+3V3", "FLASH_CS", "CS 풀업")
U_SUP = P("U", "ic", "MCP130-300", "전압 감시 리셋 (약 3.0 V)", symbol="Power_Supervisor:MCP130-xxxxTT", mpn="MCP130T-300I/TT",
          manufacturer="Microchip", reason="오픈 드레인 RST(내부 풀업) → NRST 직결, 리셋 유지 약 350 ms (임계값·지연은 데이터시트 표로 확정)",
          basis="requirement", request="SCU-005 전원 감시: Brownout / Power Good")
N("NRST", U_SUP["~{RST}"]); N("+3V3", U_SUP["VDD"]); N("GND", U_SUP["VSS"])
C("100nF/16V", "+3V3", "GND", "감시 IC 디커플링")
SW1 = P("SW", "switch", "RESET", "수동 리셋 버튼 (NRST를 GND로)", symbol="Switch:SW_Push", package="TL3342")
N("NRST", SW1[1]); N("GND", SW1[2])
U_WDT = P("U", "ic", "TPS3430", "윈도 워치독 (독립)", symbol="Power_Supervisor:TPS3430WDRC", mpn="TPS3430WDRCR",
          manufacturer="Texas Instruments", reason="너무 늦은 갱신과 너무 이른 갱신(폭주)을 모두 잡는 윈도 방식, 오픈 드레인 WDO",
          basis="requirement", request="SCU-004 독립 HW Watchdog")
N("+3V3", U_WDT["VDD1"], U_WDT["VDD2"]); N("GND", U_WDT[5], U_WDT[11])
N("WDT_WDI", U_WDT["WDI"]); N("NRST", U_WDT[8])
R("10k", "+3V3", "WDT_CWD", "CWD: 공장 설정 시간 (데이터시트 표로 확정)")
N("WDT_CWD", U_WDT["CWD"])
R("10k", "WDT_SET0", "GND", "SET0 스트랩")
R("10k", "+3V3", "WDT_SET1", "SET1 스트랩")
N("WDT_SET0", U_WDT["SET0"]); N("WDT_SET1", U_WDT["SET1"])
no_connect += [U_WDT["CRST"], U_WDT["NC"]]
C("100nF/16V", "+3V3", "GND", "워치독 디커플링")
note("FRAM: config, fault log", [U_FRAM])
note("NOR flash: FW image A/B", [U_FLASH])
note("Reset: 3.07V supervisor + window WDT", [U_SUP, U_WDT])

# =============================================================================================== Communication
page = "Ethernet"
U_PHY = P("U", "ic", "LAN8742A", "Ethernet PHY (RMII)", symbol="Interface_Ethernet:LAN8742A", mpn="LAN8742A-CZ-TR",
          manufacturer="Microchip", reason="10/100 RMII, 25 MHz 크리스탈로 50 MHz REF_CLK를 MCU에 공급",
          basis="requirement", request="SCU-006 제어통신 Ethernet (SCU, Host, Generator)")
N("+3V3_PHY", U_PHY["VDD1A"], U_PHY["VDD2A"]); N("+3V3", U_PHY["VDDIO"]); N("PHY_VDDCR", U_PHY["VDDCR"]); N("GND", U_PHY["VSS"])
FB2 = P("FB", "inductor", "600R@100MHz", "PHY 아날로그 전원 분리", symbol="Device:FerriteBead", package="0603")
N("+3V3", FB2[1]); N("+3V3_PHY", FB2[2])
C("10uF/10V", "+3V3_PHY", "GND", "PHY 아날로그 벌크", package="0805")
C("100nF/16V", "+3V3_PHY", "GND", "VDD1A 디커플링")
C("100nF/16V", "+3V3_PHY", "GND", "VDD2A 디커플링")
C("100nF/16V", "+3V3", "GND", "VDDIO 디커플링")
C("1uF/10V", "PHY_VDDCR", "GND", "VDDCR (내부 1.2 V) 벌크")
C("470pF/50V", "PHY_VDDCR", "GND", "VDDCR 고주파")
Y3 = P("Y", "crystal", "25MHz", "PHY 크리스탈", symbol="Device:Crystal_GND24", package="3225")
N("PHY_XI", Y3[1]); N("PHY_XO", Y3[3]); N("GND", Y3[2], Y3[4])
C("15pF/50V", "PHY_XI", "GND", "PHY 크리스탈 부하 C")
C("15pF/50V", "PHY_XO", "GND", "PHY 크리스탈 부하 C")
N("PHY_XI", U_PHY["XTAL1/CLKIN"]); N("PHY_XO", U_PHY["XTAL2"])
R("10k", "PHY_LED2", "GND", "nINTSEL=0: REFCLKO 출력 모드")
R("10k", "PHY_LED1", "GND", "REGOFF=0: 내부 1.2 V 레귤레이터 사용")
R("10k", "PHY_RXER", "GND", "PHYAD0=0")
N("PHY_LED2", U_PHY[2]); N("PHY_LED1", U_PHY[3]); N("PHY_RXER", U_PHY[10])
N("ETH_RXD1", U_PHY[7]); N("ETH_RXD0", U_PHY[8]); N("ETH_CRS_DV", U_PHY[11]); N("ETH_MDIO", U_PHY["MDIO"]); N("ETH_MDC", U_PHY["MDC"])
R("1.5k", "+3V3", "ETH_MDIO", "MDIO 풀업")
R("22", "PHY_REFCLK", "ETH_REF_CLK", "REF_CLK 직렬 감쇠")
N("PHY_REFCLK", U_PHY[14]); N("PHY_RST_N", U_PHY[15])
R("10k", "+3V3", "PHY_RST_N", "PHY 리셋 풀업")
N("ETH_TX_EN", U_PHY["TXEN"]); N("ETH_TXD0", U_PHY["TXD0"]); N("ETH_TXD1", U_PHY["TXD1"])
N("ETH_TXP", U_PHY["TXP"]); N("ETH_TXN", U_PHY["TXN"]); N("ETH_RXP", U_PHY["RXP"]); N("ETH_RXN", U_PHY["RXN"])
for sig in ("ETH_TXP", "ETH_TXN", "ETH_RXP", "ETH_RXN"):
    R("49.9", sig, "+3V3_PHY", f"{sig[4:]} 바이어스·종단 (1 %)")
R("12.1k", "PHY_RBIAS", "GND", "RBIAS (1 %)")
N("PHY_RBIAS", U_PHY["RBIAS"])
J_ETH = P("J", "connector", "ETH", "Ethernet (마그네틱 내장 RJ45)", symbol="Connector:RJ45_Hanrun_HR911105A_Horizontal",
          mpn="HR911105A", manufacturer="HanRun")
N("ETH_TXP", J_ETH[1]); N("ETH_TXN", J_ETH[2]); N("ETH_RXP", J_ETH[3]); N("ETH_RXN", J_ETH[6])
N("ETH_CT", J_ETH[4], J_ETH[5]); N("CHASSIS", J_ETH[8], J_ETH["SH"])
C("100nF/16V", "ETH_CT", "GND", "센터탭 필터")
R("0", "ETH_CT", "+3V3_PHY", "센터탭 바이어스 (레퍼런스 대조)")
no_connect += [J_ETH[7], J_ETH[9], J_ETH[10], J_ETH[11], J_ETH[12]]
R("1M", "CHASSIS", "GND", "섀시–GND 블리더")
C("1nF/2kV", "CHASSIS", "GND", "섀시–GND 고주파 결합", package="1206")
note("LAN8742A RMII, 50MHz REFCLKO", [U_PHY])

page = "Fieldbus_Sync"

cans = []
for k, (tx, rx) in enumerate([("FDCAN1_TX", "FDCAN1_RX"), ("FDCAN2_TX", "FDCAN2_RX")], start=1):
    U_CAN = P("U", "ic", "TCAN332", f"CAN FD 트랜시버 {k}", symbol="Interface_CAN_LIN:TCAN332", package="SOIC-8", mpn="TCAN332DR",
              manufacturer="Texas Instruments", reason="3.3 V 단전원 CAN FD 5 Mbps, ±12 V 버스 보호",
              basis="requirement", request="SCU-006 제어통신 CAN FD (Motor, Table, Sensor)")
    cans.append(U_CAN)
    N(tx, U_CAN["TXD"]); N(rx, U_CAN["RXD"]); N("+3V3", U_CAN["VCC"]); N("GND", U_CAN["GND"])
    N(f"CAN{k}_H", U_CAN["CANH"]); N(f"CAN{k}_L", U_CAN["CANL"])
    no_connect += [U_CAN[5], U_CAN[8]]
    C("100nF/16V", "+3V3", "GND", f"CAN{k} 디커플링")
    R("120", f"CAN{k}_H", f"CAN{k}_TERM", f"CAN{k} 종단 (버스 끝일 때만)", package="1206")
    JP = P("JP", "connector", "TERM", f"CAN{k} 종단 점퍼", symbol="Jumper:Jumper_2_Open", pin_count=2, package="2.54mm 헤더")
    N(f"CAN{k}_TERM", JP[1]); N(f"CAN{k}_L", JP[2])
    D_ESD = P("D", "diode", "NUP2105L", f"CAN{k} ESD 보호", symbol="Power_Protection:NUP2105L", mpn="NUP2105LT1G", manufacturer="onsemi")
    N(f"CAN{k}_H", D_ESD[1]); N(f"CAN{k}_L", D_ESD[2]); N("GND", D_ESD[3])
    J_CAN = P("J", "connector", f"CAN{k}", f"CAN{k} 단자", pin_count=3, package="5.0mm 단자대")
    N(f"CAN{k}_H", J_CAN[1]); N(f"CAN{k}_L", J_CAN[2]); N("GND", J_CAN[3])
note("CAN FD x2, 120R by jumper", cans)

trig = []
for k, (drv, rcv, din, rout) in enumerate([("TRIG1", "SYNC", "TRIG1", "SYNC_IN"), ("TRIG2", "EXPO", "TRIG2", "EXPO_FB")], start=1):
    U_422 = P("U", "ic", "THVD1451", f"RS-422 트랜시버 {k} (트리거 출력 / 동기 입력)", symbol="Interface_UART:THVD1451D",
              mpn="THVD1451DR", manufacturer="Texas Instruments", reason="전이중, 50 Mbps, 수신 페일세이프 내장: µs급 HW 트리거",
              basis="requirement", request="SCU-010 동기화 출력: FPGA/Timer 기반 HW Trigger")
    trig.append(U_422)
    N("+3V3", U_422["VCC"]); N("GND", U_422["GND"]); N(din, U_422["D"]); N(rout, U_422["R"])
    N(f"{drv}_P", U_422["Y"]); N(f"{drv}_N", U_422["Z"]); N(f"{rcv}_P", U_422["A"]); N(f"{rcv}_N", U_422["B"])
    C("100nF/16V", "+3V3", "GND", f"RS-422 {k} 디커플링")
    R("10k", din, "GND", f"{din} 풀다운 (리셋 중 출력 Low)")
    R("120", f"{rcv}_P", f"{rcv}_N", f"{rcv} 수신 종단")
J_SYNC = P("J", "connector", "SYNC", "트리거·동기 커넥터 (Timing 보드)", pin_count=10, package="2.54mm 헤더")
for i, net in enumerate(["TRIG1_P", "TRIG1_N", "TRIG2_P", "TRIG2_N", "SYNC_P", "SYNC_N", "EXPO_P", "EXPO_N", "GND", "GND"], start=1):
    N(net, J_SYNC[i])
note("RS-422 trigger out / sync in", trig)

# =============================================================================================== Isolated I/O
page = "Isolated_IO"


def iso_input(k_name: str, mcu_net: str, field: str, ret: str):
    """The validated 24 V isolated input channel (examples/iso-24v-input): ON 15-30 V, turn-on about 6.1 V."""
    d = P("D", "diode", "1N4148W", f"{k_name} 역접속 보호", package="SOD-123")
    N(field, d["A"]); N(f"{k_name}_D", d["K"])
    R("6.2k", f"{k_name}_D", f"{k_name}_R", f"{k_name} 전류 제한", package="1206",
      calc="15 V: LED 1.09 mA, 30 V: 저항 0.11 W (examples/iso-24v-input)")
    led = P("D", "led", "GREEN", f"{k_name} 입력 표시", package="0603")
    N(f"{k_name}_R", led["A"]); N(f"{k_name}_O", led["K"])
    u = P("U", "ic", "PC817", f"{k_name} 포토커플러", symbol="Isolator:PC817", mpn="PC817C", manufacturer="Sharp")
    N(f"{k_name}_O", u[1]); N(ret, u[2]); N("GND", u[3]); N(mcu_net, u[4])
    R("3.3k", f"{k_name}_O", ret, f"{k_name} 켜짐 문턱 분로", calc="켜짐 시작 약 6.1 V > OFF 요구 5 V")
    R("10k", "+3V3", mcu_net, f"{k_name} 풀업")
    rails.append({"net": field, "voltage": 24, "load_ma": 5, "voltage_min": 15, "voltage_max": 30})
    return u


J_DI = P("J", "connector", "DI", "절연 입력 단자", pin_count=5, package="5.0mm 단자대",
         basis="requirement", request="SCU-007 디지털 입력: 절연 DI, 24 V 산업용 인터페이스")
di = []
for k in range(1, 5):
    N(f"DI{k}_IN", J_DI[k])
    di.append(iso_input(f"DI{k}", f"DI{k}", f"DI{k}_IN", "GND_FIELD"))
N("GND_FIELD", J_DI[5])
note("Isolated 24V inputs, on above ~6V", di[:1])
note("Field 0V isolated from board GND", [], "GND_FIELD")

J_DO = P("J", "connector", "DO", "보호형 출력 단자", pin_count=5, package="5.0mm 단자대",
         basis="requirement", request="SCU-008 디지털 출력: 보호형 DO, 출력 상태 피드백")
N("+24V_DO", J_DO[1])
do = []
for k in range(1, 5):
    u = P("Q", "ic", "BSP75N", f"DO{k} 보호형 로우사이드 스위치", symbol="Transistor_FET_Other:BSP75N", mpn="BSP75NH6327",
          manufacturer="Infineon", reason="과전류·과열 보호, 유도성 부하 클램프 내장(HITFET), 60 V")
    do.append(u)
    N(f"DO{k}", u["IN"]); N(f"DO{k}_OUT", u["DRAIN"], J_DO[k + 1]); N("GND", u[3], u[4])
    R("100k", f"DO{k}", "GND", f"DO{k} 풀다운 (리셋 중 꺼짐)")
    R("47k", f"DO{k}_OUT", f"DO{k}_FB", f"DO{k} 상태 피드백 분압 상단", calc="18 V → 3.2 V, 30 V → 5.3 V (5 V 내성 핀)")
    R("10k", f"DO{k}_FB", "GND", f"DO{k} 피드백 분압 하단 (단선 검출 풀다운 겸용)")
note("Protected low-side outputs + readback", do[:1])

J_NTC = P("J", "connector", "NTC", "온도 센서 입력 (NTC 10k)", pin_count=4, package="2.54mm 헤더",
          basis="requirement", request="SCU-009 아날로그 입력: 전압·전류·온도 모니터링")
for k in (1, 2):
    R("10k", "VREF_3V0", f"NTC{k}_RAW", f"NTC{k} 기준 저항 (0.1 %)")
    N(f"NTC{k}_RAW", J_NTC[2 * k - 1]); N("GND", J_NTC[2 * k])
    R("1k", f"NTC{k}_RAW", f"NTC{k}", f"NTC{k} ADC 필터 R")
    C("100nF/16V", f"NTC{k}", "GND", f"NTC{k} ADC 필터 C")

# =============================================================================================== Safety interface
page = "Safety_Interface"
J_SAF = P("J", "connector", "SAFETY", "독립 Safety Controller 인터페이스", pin_count=10, package="5.0mm 단자대",
          basis="requirement", request="안전기능은 일반 제어 SW의 정상 동작에만 의존하지 않도록 설계")
N("SAF_OK_IN", J_SAF[1]); N("XPERMIT_IN", J_SAF[2]); N("GND_SAF", J_SAF[3])
saf_in = [iso_input("SAFOK", "SAFETY_OK", "SAF_OK_IN", "GND_SAF"), iso_input("XPERM", "XRAY_PERMIT", "XPERMIT_IN", "GND_SAF")]
outs = []
for name, gpio, c_pin, e_pin in [("ALIVE", "SCU_ALIVE", 4, 5), ("XREQ", "XRAY_REQ", 6, 7)]:
    R("100k", gpio, "GND", f"{gpio} 풀다운 (리셋 중 꺼짐)")
    R("1k", gpio, f"{name}_A", f"{name} 포토커플러 LED 저항", calc="(3.3 − 1.0~1.4) V / 1k = 1.9~2.3 mA")
    u = P("U", "ic", "PC817", f"{gpio} 절연 출력", symbol="Isolator:PC817", mpn="PC817C", manufacturer="Sharp")
    outs.append(u)
    N(f"{name}_A", u[1]); N("GND", u[2]); N(f"{name}_C", u[4], J_SAF[c_pin]); N(f"{name}_E", u[3], J_SAF[e_pin])
    rails.append({"net": gpio, "voltage": 3.3, "load_ma": 0})
Q_RST = P("Q", "transistor", "BSS138", "리셋 상태 출력 스위치", symbol="Transistor_FET:BSS138", mpn="BSS138", package="SOT-23")
N("NRST", Q_RST["G"]); N("GND", Q_RST["S"]); N("RSTOK_K", Q_RST["D"])
R("1k", "+3V3", "RSTOK_A", "RSTOK 포토커플러 LED 저항")
u = P("U", "ic", "PC817", "리셋·워치독 상태 절연 출력 (NRST High = 정상)", symbol="Isolator:PC817", mpn="PC817C", manufacturer="Sharp")
outs.append(u)
N("RSTOK_A", u[1]); N("RSTOK_K", u[2]); N("RSTOK_C", u[4], J_SAF[8]); N("RSTOK_E", u[3], J_SAF[9])
no_connect.append(J_SAF[10])
note("Safety I/F isolated: SCU only requests", [J_SAF])
note("Heartbeat, X-ray request, reset state", outs[:1])

# ------------------------------------------------------------------------------------------------ unused MCU pins
used = {t.split(".")[1] for pins in nets.values() for t in pins if t.startswith(U1.ref + ".")}
for p in sym.pins:
    if p.number not in used and p.number not in {"38", "51", "61", "83", "94", "107", "120", "130"}:
        no_connect.append(U1[p.number])

rails += [
    {"net": "24V_IN", "voltage": 24, "load_ma": 1200, "voltage_min": 18, "voltage_max": 30},
    {"net": "+24V", "voltage": 24, "load_ma": 1200, "voltage_min": 18, "voltage_max": 30},
    {"net": "+24V_DO", "voltage": 24, "load_ma": 1000, "voltage_min": 18, "voltage_max": 30},
    {"net": "+3V3", "voltage": 3.3, "load_ma": 600},
    {"net": "+3V3A", "voltage": 3.3, "load_ma": 10},
    {"net": "+3V3_PHY", "voltage": 3.3, "load_ma": 60},
    {"net": "VREF_3V0", "voltage": 3.0, "load_ma": 1},
]


def main():
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent
    out.mkdir(parents=True, exist_ok=True)
    resolver = PartResolver(LIB, symbol_index([LIBDIR], None), None, None)
    resolved, unresolved = [], []
    for d in parts:
        r, why = resolver(d)
        (resolved.append(r) if r else unresolved.append(f"{d['ref']}: {why}"))
    if unresolved:
        print("UNRESOLVED", *unresolved, sep="\n  ")
        return
    connections = {"nets": [{"name": k, "pins": v} for k, v in nets.items()], "no_connect": no_connect, "rails": rails,
                   "schematic_notes": notes}
    circuit, problems = build_circuit(resolved, connections, project_id="scu-main-board",
                                      title="CT SCU Main Control Board v0.1", model="hand")
    if problems:
        print("PROBLEMS", *problems[:40], sep="\n  ")
        return
    data = circuit.model_dump(mode="json")
    ids = ["power", "mcu", "memory", "ethernet", "fieldbus", "io", "safety"]
    data["sheets"] = [{"id": ids[i], "name": name, "components": [r.lower() for r in page_of if page_of[r] == name]}
                      for i, name in enumerate(PAGES)]
    (out / "circuit.json").write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    missing = [p.ref for p in resolved if not p.footprint_id]
    print(f"parts {len(resolved)}, nets {len(nets)}, no-connect {len(no_connect)}, footprints missing {missing}")


if __name__ == "__main__":
    main()
