# JCC-CCM

JCC솔루션 자체 스마트 판넬 모니터링 펌웨어 + 플랫폼.

Turck IM18-CCM50 (개방형 Debian 리눅스 게이트웨이) 위에서 동작하는 **JCC 자체 펌웨어**와,
그 데이터를 받는 **JCC 서버/대시보드**로 구성된다. 울랄라랩 Wim-X를 대체하여 데이터 주권을
JCC가 갖는 것이 목표.

> ⚠️ **클린룸 개발 원칙**: 이 프로젝트의 모든 코드는 공개 규격(Turck 공식 매뉴얼, Modbus/RS485
> 표준, Banner 센서 데이터시트)만을 근거로 처음부터 작성한다. 울랄라랩이 개발한 펌웨어의
> 코드를 참조·복사하지 않는다. 그래야 제품을 JCC가 온전히 소유하고 판매할 수 있다.

## 하드웨어 (대상 장비)

| 항목 | 사양 |
|---|---|
| 모델 | Turck IM18-CCM50-MTI/24VDC (품번 100022405) |
| OS | Debian Linux |
| CPU | TI Sitara AM3358 (ARM Cortex-A8, 32bit) |
| RAM / 저장 | 1GB DDR3L / 8GB eMMC |
| 네트워크 | 1× 1GbE (ETH0, 클라우드 업링크) |
| 필드버스 | 1× CAN/RS485 (RJ45) — 외부 센서, Modbus RTU |
| I/O | 디지털 2, 아날로그 2, 릴레이 1 |
| 내장센서 | 온도·습도·도어(거리) |

## 아키텍처

```
                          ┌── CCM-A (Debian) ──┐
[Banner 센서] --RS485-->  │  jcc_ccm 펌웨어    │ --MQTT/HTTPS--┐
                          └────────────────────┘               │
  한 판넬 =                ┌── CCM-B (Debian) ──┐               ▼
  CCM 여러 대   --RS485--> │  jcc_ccm 펌웨어    │ ----------> JCC 서버 --> 대시보드/앱
                          └────────────────────┘             (server/)   판넬 단위로 묶어 표시
```

**판넬 그룹**: CCM 1대는 윗면 RS485/CAN 1버스로 소수의 센서만 수용한다(윗면 케이블 2개
= ETH0 + RS485/CAN, 아랫면 = 24V 전원). 센서가 늘면 CCM을 추가하며, 같은 판넬의 CCM들은
config의 `panel` 값을 공유해 대시보드에서 하나의 판넬로 묶여 표시된다.

## 저장소 구조

```
firmware/          CCM 안에서 도는 펌웨어 (파이썬)
  jcc_ccm/         패키지 본체
    config.py        설정 로딩·검증
    sensors/         센서 읽기 (내장센서 + Modbus 외부센서)
    transport/       데이터 전송 (MQTT / HTTP)
    agent.py         메인 수집 루프
  config/          설정 파일 예시 (config.example.toml)
  scripts/         설치·배포 스크립트
server/            JCC 수신 서버 + 대시보드 (추후)
docs/              규격 정리, 배포 절차
```

## 개발 상태

- [x] 하드웨어·접속 규격 파악 (Turck 공식 매뉴얼)
- [x] 펌웨어 뼈대: 설정 → 센서읽기 → 전송 루프 (테스트 12개 통과)
- [x] 내장 센서 드라이버 (ambient/distance) — Turck 기본 스크립트 호출
- [x] Modbus RTU 외부 센서 드라이버
- [x] MQTT / HTTP 전송
- [x] JCC 수신 서버 (표준 라이브러리, SQLite)
- [x] 대시보드 (실시간 타일 + 이력 차트, 외부 CDN 없음)  ← **지금 여기**
- [ ] CCM 실기 검증 (새 CCM 도착 후)
- [ ] 서버 인증·다중 현장·경보 알림 고도화

## 접속 (개발용, Turck 공장 기본값)

```
SSH  sshu@<device-ip> -p 1522    (기본 IP 192.168.1.20, 기본 비번은 매뉴얼 참조 후 즉시 변경)
```
