# 고객 화면 JCC GUARD Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 고객 계정이 보는 별도 화면 `/guard` — 안심 지수 계기판(밤/낮), 판넬 상세, 이번 달 지켜낸 것, 위험 순간, 자체 로그인, 영업 시연판.

**Architecture:** 서버 `guard.py`가 기존 계산(fleet·build_report·_alarm_stats·rul·events)을 재사용해 고객 화면 모델을 만든다.
`app.py`에 `/guard`(화면)와 `/api/guard*`(데이터). 화면은 단일 파일 `server/static/guard.html`. 시연판은 `tools/build_web.py`가
`web/guard*.html`을 만들고 `web/demo-api.js`가 같은 모양으로 답한다.

**Tech Stack:** Python 3 stdlib 서버, 바닐라 JS 단일 HTML, 테스트는 `python -m tests.<name>`(server/에서).

## Global Constraints

- 숫자는 실제 기록에서만. 가정이 든 값(순찰 비교)은 화면에 "추정"과 기준(하루 3회)을 적는다.
- 고객 조작은 보기 + 경보 확인/메모만. 출력(벤트·히터·팬) 조작 없음.
- 고객 범위: 고객 계정은 자기 고객사 판넬만. 직원(admin)은 `customer_id`로 미리보기.
- 한국어 UI, 휴대폰 375px 가로 넘침 없음, 밤/낮 둘 다, 상태는 색 + 글자.
- 이모지 아이콘 금지, 레이아웃 속성 애니메이션 금지, CSS 접두사 `gd-`(guard.html은 별도 파일이라 충돌 없음).
- 테스트는 실제 LAN IP를 쓰지 않는다.

---

### Task 1: guard.py — 안심 지수·상태 문장·판넬 모델·위험 순간·이번 달

**Files:** Create `server/jcc_server/guard.py`, Test `server/tests/test_guard.py`

**Interfaces — Produces:**
- `score(rows: list, now: float) -> {"score": int, "color": "ok"|"warn"|"crit", "items": [{"text","minus"}], "checks": [{"label","value"}]}`
- `state_line(rows: list) -> {"title": str, "sub": str}`
- `guard_view(storage, panels: set|None, site: str, now: float|None=None) -> dict`
  (`site, index, state, panels[], incident|None, contact, next_inspection, month`)
- `month_view(storage, panels: set|None, period: str, now: float|None=None) -> dict`
- `panel_detail(storage, panel: str, now: float|None=None) -> dict|None` (지표 + 7일 타임라인)

감점표(설계서): 위험 −20/건, 주의 −5/건, 끊긴 CCM −10/대, 화재 지켜봄 −5, 결로·단자 발열 주의 −3, 남은 여유 60일 이내 −3, 점검 기한 지남 −5. 0 하한.
색: 위험 경보 있으면 crit, 감점 있으면 warn, 없으면 ok.

- [ ] 실패하는 테스트 작성(감점 각 항목·하한·색·상태 문장·고객 범위·위험 순간 단계·월 장부 출처·빈 판넬)
- [ ] 실행해 실패 확인
- [ ] `guard.py` 구현(fleet 행을 입력으로 쓰는 순수 함수 `score`·`state_line` + 저장소를 읽는 `guard_view`·`month_view`·`panel_detail`)
- [ ] 통과 확인 · 커밋

### Task 2: 경로·권한 — `/guard`, `/api/guard`, `/api/guard/month`, `/api/guard/panel`

**Files:** Modify `server/jcc_server/app.py`(ROUTES·do_GET), Test `server/tests/test_guard.py`(HTTP 절), `server/tests/test_menu_routes.py`

- `/guard` 공개(화면 껍데기), 나머지 read. 고객은 자기 범위, 직원은 `customer_id`(없으면 전체). `panel`은 범위 검사 후 404.
- [ ] HTTP 테스트(401·고객 범위·직원 미리보기·남의 판넬 404) → 구현 → 통과 · 커밋

### Task 3: 고객 화면 `server/static/guard.html`

화면: 로그인(⑧) · 첫 화면(①) · 지수 근거 시트(⑤) · 판넬 상세(②) · 이번 달(③, 달 넘기기, 월간·점검 보고서 보기 시트) ·
위험 순간(⑥, "JCC에 전화"=tel, "현장 확인했어요"=ack+메모, 담당자만) · PC 2단(④) · 밤/낮(시스템 따름 + 직접 선택, localStorage).
5초 갱신, 연결 끊김 회색 링. 직원은 머리에 고객사 고르기(미리보기).
- [ ] 화면 작성 → 데모·실서버 리그에서 휴대폰/PC·밤/낮·위험 순간 확인 · 커밋

### Task 4: 직원 화면 연결

**Files:** Modify `server/static/index.html`
- 고객 계정(manager/viewer)이 `/`에 로그인하면 `/guard`로. 직원 도구 메뉴에 "고객 화면으로 보기"(새 탭 `/guard`).
- [ ] 구현 → 확인 · 커밋

### Task 5: 시연판 — demo-api `/api/guard*` + "그날 밤" 이야기 3편 + 빌드

**Files:** Modify `web/demo-api.js`, `tools/build_web.py`; 생성 `web/guard.html`, `web/guard-artifact.html`
- 이야기: 화재 징조(fire) · 단자 과열(contact) · 장마철 결로(dew) — 기존 `JCC_DEMO.episode` + 자막 타임라인.
- [ ] 구현 → 데모 확인 → 별도 아티팩트 공개 · 커밋

### Task 6: 글꼴·디자인 문서·마무리

- Gothic A1 Light(300) — 사용자 승인 후 내려받아 `static/fonts/`(OFL). 승인 전엔 400으로 보인다.
- DESIGN.md에 '고객 화면' 절, PRODUCT.md 사용자 절 갱신(고객 화면 = 고급 계기판, 이모지 계획 폐기), 메모리 갱신.
- [ ] 전체 테스트 · 디자인 검사 · 커밋
