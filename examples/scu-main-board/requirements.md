# CT SCU (System Control Unit) 메인 제어보드 — 회로 요구사항 v0.1

출처: 차세대 의료용 CT HW 요구사항 정의서(HRS Rev 0.1)의 SCU 항목(SCU-001~012)과 전원·통신·동기화·안전 요구 중 메인보드에 해당하는 것.
기준 플랫폼 Dual-source Photon-counting CT. 이 보드는 Gantry·X-ray·DAS·Table을 직접 구동하지 않고 상태를 모아 촬영 순서를 정하며,
방사선 조사 차단은 독립 Safety Controller가 맡는다.

## 전원

- 입력: Power Distribution Board에서 24 VDC(18~30 V), 보호접지 별도. 입력 퓨즈, 역극성 보호, 서지 TVS(PWR-004, PWR-005).
- 보드 내부: 3.3 V(MCU, PHY, 통신 IC), ADC용 3.0 V 기준전압과 분리된 아날로그 3.3 V(PWR-107).
- 24 V 입력 전압·전류, DO 전원, 3.3 V를 ADC로 감시(각 Rail 전압·전류 감시).
- 입력 저전압에서 벅 컨버터가 깔끔하게 꺼질 것(UVLO), 전원 이상 시 MCU 리셋(SCU-005).

## 제어

- SCU-001 산업용 MCU: CAN FD 2채널, Ethernet MAC, 고분해능 타이머, ADC를 한 칩에.
- SCU-003 비휘발성 메모리: 설정·진단·Fault 이력(정전 직전 기록 가능), FW 이미지 저장(A/B, 복구).
- SCU-004 독립 HW Watchdog: MCU 내부 워치독과 별개, 폭주(너무 이른 갱신)도 검출.
- SCU-005 전원 감시: 3.3 V 저전압 리셋.
- SCU-011/012: SWD 디버그, UART 콘솔, 수동 리셋.

## 통신·동기화

- SCU-006 CAN FD 2채널(Motor, Table, Sensor), 버스 끝일 때만 120 Ω 종단(점퍼), ESD 보호.
- Ethernet 10/100(Host, Generator), 마그네틱 내장 RJ45.
- SCU-010 HW 트리거: Timing/Sync 보드로 트리거 출력 2개, 동기 입력·조사 피드백 입력 2개, 차동(RS-422), 리셋 중 정해진 상태.
- 마이크로초급 동기화는 CAN/Ethernet 메시지에 의존하지 않음(COM 요구).

## I/O

- SCU-007 절연 디지털 입력 4채널: 24 V, 15~30 V ON, 5 V 이하 OFF, 역접속 보호, 입력 표시.
- SCU-008 보호형 디지털 출력 4채널: 단락·과열 보호, 출력 상태 피드백(단선·단락 검출), 리셋 중 꺼짐.
- SCU-009 아날로그 입력: 전압·전류 감시 + NTC 온도 2채널.

## 안전 인터페이스

- 안전기능은 일반 제어 SW의 정상 동작에만 의존하지 않는다. SCU는 X-ray 조사를 **요청**만 하고, 허가는 독립 Safety Controller가 한다.
- SCU → Safety Controller(절연): 생존 신호(Heartbeat), X-ray 요청, 리셋·워치독 상태(하드웨어가 직접 만드는 신호).
- Safety Controller → SCU(절연 입력): 안전 상태 OK, X-ray 허가 상태(읽기 전용, 피드백).
- 리셋·부팅 중 모든 출력은 꺼진 상태(요청 없음).
