# 예지 튜닝 콘솔 + 원격 설정 적용 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 예지 기준값 손잡이 → 시나리오 재생 A/B·성적표를 즉시 보여주고, 놓친 사고 0건일 때만 서버·CCM에 버전 설정으로 적용한다.

**Architecture:** 파라미터 레지스트리(`params.py`)가 손잡이·기본·한계·관계를 한 곳에 정의하고 펌웨어에 바이트 동일 사본으로 들어간다. 시나리오(`scenarios.py`)와 평가기(`tuning_eval.py`)는 서버 정본이며, 브라우저 미리보기는 새로 분리한 `predict-core.js`(데모와 실서버 화면이 같이 쓰는 JS 코어)로 같은 재생을 돌린다. 적용은 서버가 파이썬으로 재검증해 관문을 쥐고, 텔레메트리 응답으로 CCM에 전달한다.

**Tech Stack:** Python 3 표준 라이브러리만(서버·펌웨어), 바닐라 JS(외부 CDN 0), SQLite, Node(패리티 테스트 하네스).

## Global Constraints

- 서버·펌웨어는 표준 라이브러리만. 대시보드 외부 CDN 0개.
- 코어 파일은 `tools/sync_edge_core.py`로 펌웨어에 바이트 동일 복사 — `params.py`를 FILES에 추가, `--check` 통과 필수.
- 기본값 동작 불변: 레지스트리 도입 뒤에도 기존 테스트 전부 통과(FireConfig 등 기본값 그대로).
- 관문: 놓친 사고 0건 + 한계·관계 통과 → 서버가 판정. 브라우저 결과는 참고.
- 절대 한계는 `params.py`에만(펌웨어 사본은 OTA로만 바뀜).
- 시나리오는 코드로만 변경. 대시보드 편집 불가.
- 새 UI 금지 패턴: side-tab, dark-glow, 레이아웃 속성 애니메이션, 이모지 아이콘. 짧은 CSS 클래스는 접두사(`tn-`)로 충돌 방지.
- 비밀값(알리고 키 등) 코드·저장소 금지. 푸시·배포는 사용자 요청 시에만.
- 커밋 메시지 끝: `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`

## File Structure

| 파일 | 책임 |
|---|---|
| `server/jcc_server/params.py` (신규, 코어) | 레지스트리 `PARAMS`, `defaults()`, `validate(params)->str|None`, `apply_to(fire,contact,dew,params)`, `from_cfgs(fire,contact,dew)->dict`, `registry_view()` |
| `server/jcc_server/scenarios.py` (신규) | `SCENARIOS` 11개, `build(name)->{roles:{role:[(ts,v)]}, step, t_ref, expect}`, `export_all()` |
| `server/jcc_server/tuning_eval.py` (신규) | `replay(scn, params)->{events, trace}`, `judge(scn, run)->{ok, lead_min, …}`, `scorecard(params)->{caught, missed, false_alarms, watch_in_traps, lead_avg, per:{…}}` |
| `server/jcc_server/predict.py` | `Predictor.reconfigure(params)` (cfg 제자리 갱신, 상태 유지) |
| `server/jcc_server/storage.py` | `tuning_config` 테이블, `tuning_active()`, `tuning_history()`, `tuning_activate(params, author, note, scorecard)`, CCM별 `note_tuning(dev, rep)`, `tuning_offer_for(dev, ver)` |
| `server/jcc_server/app.py` | `/api/tuning/{params,scenarios,config,evaluate,apply,rollback}`, `/predict-core.js`, 텔레메트리 응답 `tuning` |
| `server/static/predict-core.js` (신규) | JS 코어 한 벌: 판정 3종(cfg 주입) + ContactBaseline + VentController/DewActuator + buildPanelInputs + Predictor + replay/judge/scorecard. `globalThis.JCCPredict` |
| `web/demo-api.js` | 자체 코어 제거 → `JCCPredict` 사용, `/api/tuning/*` 데모 구현(`JCC_TUNING_DATA`) |
| `tools/build_web.py` | predict-core.js 복사·주입, `tuning-data.js` 생성, artifact 인라인 |
| `server/static/index.html` | 튜닝 오버레이(`tn-`), 메뉴 버튼, 이벤트 라벨 |
| `firmware/jcc_ccm/edge.py`, `transport/http_transport.py`, `agent.py` | 설정 수신·검증·적용·저장(`tuning.json`)·보고 |
| 테스트 | `server/tests/test_params.py`, `test_tuning_eval.py`, `test_tuning_api.py`, `test_parity.py`(확장), `parity_harness.js`(predict-core로), `firmware/tests/test_tuning.py` |

---

### Task 1: 파라미터 레지스트리

**Files:** Create `server/jcc_server/params.py`, `server/tests/test_params.py`; Modify `tools/sync_edge_core.py` (FILES += params)

**Interfaces — Produces:**
- `PARAMS: list[dict]` — 각 항목 `{key, group, label, unit, default, ui:(lo,hi,step), hard:(lo,hi), desc}`; key는 `fire.h2.warn`, `fire.w_h2`, `fire.open_fri`, `contact.res_warn`, `dew.margin_alarm` 형식(설계 5절 표 전 항목, 총 37개).
- `RELATIONS: list[(a_key, "<", b_key)]` — 설계 5절 관계 규칙.
- `defaults() -> dict[str, float]`
- `validate(params: dict) -> str | None` — 빠진 키·모르는 키·숫자 아님·NaN·hard 밖·관계 위반이면 한국어 사유.
- `from_cfgs(fire, contact, dew) -> dict`, `apply_to(fire, contact, dew, params) -> None`(제자리 대입).
- `registry_view() -> dict` — `{params:[…], relations:[…]}` JSON용.

- [ ] Step 1: 테스트 작성 — `defaults() == from_cfgs(FireConfig(), ContactCfg(), DewCfg())`; `validate(defaults()) is None`; 각 기본값이 ui·hard 범위 안; 빠진 키/모르는 키/`"abc"`/`float("nan")`/`open_fri=71`/`close_fri>=watch_fri`/`h2.warn>=h2.alarm` 각각 사유 문자열; `apply_to` 후 `from_cfgs`가 새 값과 같음; 관계 규칙이 기본값에서 성립.
- [ ] Step 2: 실행 → ImportError로 실패 확인 (`cd server && python -m tests.test_params`)
- [ ] Step 3: `params.py` 구현(설계 5절 한계 그대로, 화면 범위는 한계 안쪽).
- [ ] Step 4: 테스트 통과 + `python tools/sync_edge_core.py` 후 `--check` 동일.
- [ ] Step 5: 커밋 `예지 파라미터 레지스트리 — 손잡이·기본·절대 한계·관계 규칙 한 곳에`

### Task 2: JS 코어 분리 (`predict-core.js`)

**Files:** Create `server/static/predict-core.js`; Modify `web/demo-api.js`, `server/tests/parity_harness.js`, `server/tests/test_parity.py`, `tools/build_web.py`, `server/jcc_server/app.py`(`/predict-core.js` 공개 서빙)

**Interfaces — Produces (`globalThis.JCCPredict`):**
- `defaultCfg() -> {fire:{h2:{warn,alarm,rise_warn,rise_alarm},voc,co,temp_rise_warn,…,watch_fri}, contact:{i_min,…,tau_days}, dew:{…}}` — 파이썬 필드명 그대로.
- `cfgFromParams(params) -> cfg` (레지스트리 키 → cfg).
- `assessFire(sig, cfgFire)`, `assessContact(cur,temp,amb,hist,cfgContact,kRef,learning)`, `assessDew(temp,rh,surface,hist,cfgDew)`, `slopePerMin`, `robustZ`, `dewPoint`
- `class ContactBaseline(cfgContact, state?)`, `class VentController(cfgFire)`, `class DewActuator()`
- `buildPanelInputs(roles, series, now) -> {inputs, pending, reps}` (inputs.py 미러)
- `class Predictor(cfg)` → `assessPanel(panel, now, inputs, pending)`, `setActuator`, `actuators`, `reconfigure(cfg)`
- `groupAlarms`(demo-api에서 이동하지 않음 — 그대로 둔다)

- [ ] Step 1: 하네스를 `predict-core.js` + `demo-api.js` 순서로 로드하게 바꾸고, 코어 호출을 `JCCPredict`로(기존 픽스처 그대로). 새 픽스처 `panel`: 합성 시계열(역할 8개·에포크 시각·튄 값·끊김 포함) 3세트를 파이썬 `build_panel_inputs`+`Predictor.assess_panel` 한 틱씩 vs JS 동일 → 매 틱 stage·fri·residual·margin·액추에이터 비교.
- [ ] Step 2: 실행 → JCCPredict 없음으로 실패.
- [ ] Step 3: demo-api.js의 코어(ramp~dewStep, ContactBaseline, seriesF/asof/panelInputs 상태판정)를 predict-core.js로 옮기며 cfg 주입형으로. demo-api의 tickPredict는 `roles`(findSensor 결과) + `seriesOf` 어댑터 + `new JCCPredict.Predictor(데모 cfg: learn_samples 45, learn_span 60)`로 재작성, 경보·이벤트 glue는 유지.
- [ ] Step 4: app.py `/predict-core.js` 서빙(인증 게이트 예외 — 데이터 없음), index.html `<script src="predict-core.js">`를 첫 `<script>` 앞에. build_web: web/로 복사, index에서 demo-api 앞에 주입 순서 보장, artifact에서 둘 다 인라인.
- [ ] Step 5: test_parity 전체 통과 + 데모 미리보기에서 `JCC_DEMO.episode("fire")` 후 벤트 개방·콘솔 오류 0 확인.
- [ ] Step 6: 커밋 `JS 예지 코어 한 벌로 — 데모·실서버 화면·튜닝이 같은 코드`

### Task 3: 시나리오 + 평가기 (파이썬 정본)

**Files:** Create `server/jcc_server/scenarios.py`, `server/jcc_server/tuning_eval.py`, `server/tests/test_tuning_eval.py`

**Interfaces — Produces:**
- `scenarios.NAMES` (순서 고정 11개: `cable_overheat, co_char, contact_loosen, contact_jump, monsoon_dew, night_chill, spray, welding, motor_start, summer_noon, sensor_glitch`)
- `scenarios.build(name) -> {"name","label","kind":"accident"|"trap","algo":"fire"|"contact"|"dew","step":float,"t0":float,"t_end":float,"t_ref":float|None,"roles":{role:[[ts,v],...]},"expect":{...}}` — 시드 고정 `random.Random(name 해시)`로 결정적, 값 소수 2자리.
- `tuning_eval.replay(scn, params) -> {"events":[{"t","what"}], "trace":[[t, metric]]}` — what ∈ `fire_watch, fire_danger, vent_open, contact_watch, contact_danger, dew_watch, dew_danger, fan, heater`; metric은 algo별(FRI·잔차·여유), 틱 = scn.step 격자.
- `tuning_eval.judge(scn, run) -> {"ok":bool, "first":t|None, "lead_min":float|None, "why":str, "watch_hits":int}`
- `tuning_eval.scorecard(params) -> {"caught":int,"missed":int,"false_alarms":int,"watch_in_traps":int,"lead_avg":float|None,"per":{name: judge결과}}`

판정 규칙(설계 7절): 사고 = `expect.need`(예: `vent_open`)가 `t_ref` 전에 발생, 접점 급변은 `t_start+600` 이내 `contact_danger`. 함정 = `expect.forbid` 목록 중 하나라도 발생하면 오경보, `fire_watch`·`contact_watch`·`dew_watch`는 `watch_in_traps`로만 셈.

- [ ] Step 1: 테스트 — 같은 이름 두 번 build 동일; 각 시나리오 역할·t_ref·kind 형식; **기본값 scorecard: missed 0, caught 6, false_alarms 0**(CI 기준선); 사고를 놓치게 만든 설정(open_fri 70·w_h2 0.2·w_voc 0.2·pair_boost 0) → `cable_overheat` missed; 전체 scorecard 10초 이내.
- [ ] Step 2: 실행 → 실패.
- [ ] Step 3: 구현. 기본값이 기준선을 못 넘으면 **멈추고 사용자에게 보고**(시나리오를 몰래 쉽게 고치지 않는다).
- [ ] Step 4: 통과 확인.
- [ ] Step 5: 커밋 `튜닝 시나리오 11종 + 재생 평가기 — 기본값 사고 6/6·오경보 0 기준선`

### Task 4: JS 재생·성적표 + 패리티

**Files:** Modify `server/static/predict-core.js`(replay/judge/scorecard), `parity_harness.js`, `test_parity.py`

**Interfaces — Produces:** `JCCPredict.replay(scn, params)`, `JCCPredict.judge(scn, run)`, `JCCPredict.scorecard(scenarios, params)` — 파이썬과 같은 반환 형식.

- [ ] Step 1: 테스트 — `scenarios.export_all()`을 하네스에 넘겨 기본값 + 시드 고정 무작위 설정 20개(각 손잡이 ui 범위 균등, 관계 위반은 재추출)로 scorecard·per 결과·events 시각이 파이썬과 같음.
- [ ] Step 2: 실패 확인 → Step 3 구현 → Step 4 통과, 11개 전체 JS 재생 시간 측정 출력(목표 0.3초, Node 기준).
- [ ] Step 5: 커밋 `JS 재생·성적표 — 파이썬 정본과 21개 설정 일치`

### Task 5: 서버 설정 버전·API·관문

**Files:** Modify `predict.py`(reconfigure), `storage.py`, `app.py`, `monitor.py`(시작 시 활성 버전 로드); Create `server/tests/test_tuning_api.py`

**Interfaces — Produces:**
- `Predictor.reconfigure(params)` → `params.apply_to(self.fire_cfg, self.contact_cfg, self.dew_cfg, params)` (VentController·ContactBaseline이 같은 cfg 객체를 참조하므로 상태 유지된 채 반영).
- `Storage.tuning_active() -> {"version":int,"params":dict,"created_at","author","note"}` (없으면 v0 기본값)
- `Storage.tuning_activate(params, author, note, scorecard, kind) -> int` (kind: apply|rollback)
- `Storage.tuning_history(limit=20) -> list`, `Storage.tuning_edge_status() -> {dev:{version,status,error,ts}}`
- `Storage.note_tuning(dev, rep)`; 텔레메트리 응답에 `tuning` (보고 버전 ≠ 활성)
- API 설계 6절 그대로. 상태코드: 400 검사 실패(`{"error"}`), 409 관문 실패(`{"error","scorecard"}`) / 버전 충돌(`{"error","active"}`), 503 평가 초과.
- 이벤트: `tuning_apply`, `tuning_reject`, `tuning_rollback`, `tuning_edge`.

- [ ] Step 1: 테스트 — 로그인 필요(401); params·scenarios GET; evaluate 기본값; apply 정상 → v1, predictor cfg 반영, 열린 벤트 유지; 한계 밖 400; 관계 위반 400; 놓치는 설정 409 + 이벤트 tuning_reject; base_version 불일치 409; rollback default → v2 = 기본값; 재시작(새 Storage 같은 DB) 후 활성 버전 복원; 텔레메트리 응답 tuning 포함/미포함.
- [ ] Step 2~4: 실패 → 구현 → 통과. 기존 서버 테스트 전부 재실행.
- [ ] Step 5: 커밋 `설정 버전·적용 관문 API — 놓친 사고 있으면 서버가 거부`

### Task 6: CCM 수신·적용·보고

**Files:** Modify `firmware/jcc_ccm/transport/http_transport.py`(`take_tuning()`), `agent.py`(payload `tuning`, 수신 전달), `edge.py`(`apply_tuning`, `tuning.json`); Create `firmware/tests/test_tuning.py`; Modify `server/tests/test_edge_link.py`(종단 1건 추가)

**Interfaces — Produces:**
- `EdgePredictor.apply_tuning(msg: {"version":int,"params":dict}) -> {"version","status":"ok"|"rejected","error"?}` — `params.validate` → `Predictor.reconfigure` → 첫 `step` 예외 시 이전 값 복귀 → `tuning.json`(state_file 옆) 원자 저장.
- `EdgePredictor.tuning_report() -> {"version","status","error"?}`; 시작 시 `tuning.json` 복원(검증 실패면 무시하고 기본값).
- payload `tuning` = 위 보고.
- MQTT: `jcc/ccm/{id}/cmd` 메시지에 `tuning`이 있으면 같은 경로로 전달(http와 공통 `take_tuning`).

- [ ] Step 1: 테스트 — 정상 적용·보고; 한계 밖·모르는 키 거부(이전 유지); 저장 후 새 EdgePredictor 복원; 손상 파일 무시; 종단: 서버 apply → CCM tick → 서버 `tuning_edge_status`에 v1 ok, 한계 밖 강제 주입(서버 우회) → CCM rejected 기록.
- [ ] Step 2~4: 실패 → 구현 → 통과. 펌웨어·서버 전체 재실행 + sync `--check`.
- [ ] Step 5: 커밋 `CCM 원격 설정 — 절대 한계 검증 후 적용·저장·보고`

### Task 7: 튜닝 화면 + 데모

**Files:** Modify `server/static/index.html`, `web/demo-api.js`, `tools/build_web.py`(`tuning-data.js`)

- 메뉴 `튜닝 콘솔` 버튼 → `#tn-overlay`. 구성은 설계 9절·목업: 시나리오 칩, 손잡이(그룹별 range, 기본값과 다르면 강조, 더블클릭 기본값), 캔버스 타임라인 A/B, 성적표, 관문 막대(메모·기본값으로·되돌리기·적용), 버전 이력 + CCM별 상태.
- 손잡이 input → 150ms 디바운스 → `JCCPredict.scorecard` B 재계산(A는 활성 버전으로 1회 캐시).
- 적용 → `/api/tuning/apply` → 409면 놓친 시나리오 표시, 200이면 A 갱신.
- 서버 응답 scorecard가 미리보기와 다르면 "미리보기 불일치" 경고(패리티 버그 신호).
- 데모: `JCC_TUNING_DATA`(빌드 시 파이썬 export) 로 params·scenarios, apply/rollback/config 메모리 구현(관문은 JS scorecard로).
- EVENT_META·DETAIL_EVENTS에 tuning_* 추가.

- [ ] Step 1: build_web 실행, 미리보기(8899)에서: 손잡이 이동 → 성적표 변화, 벤트 기준 70 + 가중치 낮춤 → 칩 빨강·적용 409 메시지, 기본값 복귀 → 적용 성공, 콘솔 오류 0, 모바일 폭 확인. 실서버 리그(8900)에서 로그인 후 적용 → CCM 상태 표시.
- [ ] Step 2: 커밋 `튜닝 콘솔 화면 — 손잡이·A/B 타임라인·성적표·적용 관문`

### Task 8: 마무리

- [ ] 알고리즘 내역에 항목 추가, build_web, 전 테스트, 아티팩트 재게시, 커밋. 푸시는 사용자 확인 후.
