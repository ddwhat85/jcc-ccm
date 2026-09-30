# 출력 작동 확인(피드백) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 벤트·히터·팬 명령 뒤 실제 상태를 확인·재시도하고, 끝내 다르면 작동 실패 경보를 낸다.

**Architecture:** CCM의 출력마다 `ActuatorGuard`(새 파일 `firmware/jcc_ccm/guard.py`)가 명령 기한·재시도·주기 감시를 맡고,
출력 드라이버는 `read_state()`(코일 되읽기)·`read_position()`(위치 스위치)만 제공한다. 엣지 보고에 확인 상태가 실리고,
서버는 전이 때 `actuator_fault` 경보를 열고 닫는다. 화재 사건이 열린 판넬의 벤트 실패는 그 사건에 묶인다.

**Tech Stack:** Python 표준 라이브러리 + pymodbus(CCM 실기만), 바닐라 JS.

## Global Constraints
- 통신(읽기) 실패는 고장으로 단정하지 않는다 → `unknown`, 다음 주기 재시도.
- 구동기 혹사 금지: fault 중 재쓰기는 check_every(기본 30초)에 1회.
- RS485는 센서와 같은 shared_bus·락.
- 기본값: feedback modbus_coil→"coil", log→"none"; travel_seconds 벤트 30·히터/팬 3; retries 2; check_every 30.
- 기존 테스트 전부 통과, sync_edge_core --check 동일, 새 UI 금지 패턴(side-tab·glow·이모지 아이콘) 없음.
- 커밋 끝: `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`

---

### Task 1: 드라이버 읽기 + 설정
**Files:** Modify `firmware/jcc_ccm/sensors/modbus.py`(read_coils·read_discrete_inputs), `firmware/jcc_ccm/config.py`(ActuatorConfig 필드·검증),
`firmware/jcc_ccm/actuators.py`(read_state·read_position, LogActuator 고장 주입); Test `firmware/tests/test_guard.py`(설정 부분)

**Produces:**
- `ModbusBus.read_bits(slave, address, kind) -> bool` kind ∈ {"coil","discrete"} (실패 시 예외)
- `ActuatorConfig.feedback: str = ""`(""=드라이버별 기본), `feedback_slave: int = -1`(-1=릴레이 슬레이브), `feedback_input: int = -1`,
  `feedback_invert: bool = False`, `travel_seconds: float = 0`(0=종류별 기본), `retries: int = 2`, `check_every: float = 30`;
  `ActuatorConfig.feedback_mode()`, `.travel()` 해석 메서드. 검증: feedback ∈ {"", none, coil, switch}, switch면 feedback_input ≥ 0,
  coil/switch는 driver=modbus_coil만, travel 0~600, retries 0~5, check_every 5~600.
- 드라이버: `read_state() -> bool|None`(코일 논리 상태, invert 반영; 읽기 불가 None), `read_position() -> bool|None`(열림/켜짐=True),
  `LogActuator`: `sim_stuck: bool|None`(None=정상, True/False=그 위치에 고정), `sim_delay: float`(초), `sim_read_fail: bool`, `sim_clock`.

### Task 2: ActuatorGuard + 엣지 통합
**Files:** Create `firmware/jcc_ccm/guard.py`; Modify `firmware/jcc_ccm/edge.py`(_drive·보고); Test `firmware/tests/test_guard.py`

**Produces:**
- `ActuatorGuard(act, cfg, clock)`: `.commanded(on, now)`(쓰기 성공 직후 호출) · `.tick(now) -> list[event]`(이벤트 dict: {kind, text, level})
  · `.status -> "ok"|"moving"|"fault"|"unknown"|"off"` · `.position -> "open"|"closed"|None` · `.fault -> str`.
- 엣지 보고 actuators[kind] += `confirm`, `position`, `fault`. 가드 이벤트는 `actions`에 `{"actuator","event","text","level"}`로 실림.

### Task 3: 서버 경보·사건
**Files:** Modify `server/jcc_server/storage.py`(ingest_edge: confirm 전이 → raise/clear `actuator_fault`, 가드 이벤트 기록),
`server/jcc_server/incidents.py` + `web/demo-api.js`(groupAlarms 미러: 화재 규칙에 벤트 actuator_fault), `server/tests/test_incidents.py`,
`server/tests/test_parity.py`(fixture), `server/tests/test_edge_link.py`(종단: 걸림→경보→수리→해소)

### Task 4: 화면·데모·마무리
**Files:** `server/static/index.html`(예지 카드 확인 표시, EVENT_META actuator_fault·_clear), `web/demo-api.js`(episode "vent_stuck"),
알고리즘 내역, build_web, 아티팩트 재게시.
