# 고객사별 계정 분리 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 고객사 → 판넬 배정과 admin/manager/viewer 계정으로, 모든 데이터 API가 계정 범위만 돌려주게 한다.

**Architecture:** `accounts.py`(계정·세션·고객사·판넬 배정·알림 번호, storage의 연결·락 공유)가 저장을 맡고, app.py는
단일 권한 표 `ROUTES`로 요청마다 정책(public/read/operate/admin)을 정한 뒤 read 응답을 범위 필터로 거른다.
표에 없는 경로는 403. 화면은 `/api/auth/status`의 role로 메뉴를 숨기고 계정 관리 오버레이를 추가한다.

**Tech Stack:** Python 표준 라이브러리(hashlib.pbkdf2_hmac, secrets), SQLite, 바닐라 JS.

## Global Constraints
- 비번: PBKDF2-SHA256 200,000회·16바이트 솔트, 상수시간 비교. 세션 토큰 32바이트 난수, DB엔 SHA-256만, 7일 만료.
- 환경변수 계정 = 비상용 admin(기존 test_auth 그대로 통과).
- 기존 로그인 잠금(5회/300초) 유지. 오류 문구는 계정 유무를 드러내지 않음.
- 비밀값(비번·토큰) 로그·이벤트·응답에 남기지 않음(임시 비번은 생성 응답에 한 번만).
- 새 UI 금지 패턴 없음, CSS 접두사 `acct-`.
- 커밋 끝: `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`

---

### Task 1: accounts.py
**Files:** Create `server/jcc_server/accounts.py`, `server/tests/test_accounts.py`; Modify `storage.py`(`self.accounts`)
**Produces:** `hash_pw(pw)->str`, `check_pw(pw, stored)->bool`; `Accounts(conn, lock)`:
`create_customer(name)->id`, `list_customers()`, `assign_panel(panel, customer_id|None)`, `panel_owner_map()->{panel:cid}`,
`set_receivers(cid, [numbers])`, `receivers_for(cid)->list`, `create_user(username, role, customer_id)->(id, temp_pw)`,
`reset_password(uid)->temp_pw`, `set_disabled(uid, bool)`, `change_password(uid, old, new)->str|None`, `list_users()`,
`authenticate(username, pw)->user|None`, `new_session(uid)->token`, `session_user(token)->user|None`, `end_session(token)`,
`user = {id, username, role, customer_id, customer, must_change}`.

### Task 2: 권한 표·범위 필터·로그인
**Files:** Modify `server/jcc_server/app.py`, `storage.py`(build_report·_predict_report 기기 필터); Create `server/tests/test_scope.py`
**Produces:** `ROUTES`, `route_policy(method, path)->str|None`, `Handler._user()`, `Handler._scope()->set[device]|None`,
`/api/me/password`, `/api/auth/status` 확장. 필터: panels·devices·alarms·incidents·events·predict·report·history.

### Task 3: 계정 관리 API·화면
**Files:** `app.py`(`/api/admin/accounts` GET, `/api/admin/{customer,panel,receivers,user,user/reset,user/disable}` POST),
`server/static/index.html`(acct 오버레이, 비번 변경 창, 등급별 메뉴 숨김), `web/demo-api.js`(데모 메모리 구현), 테스트 확장.

### Task 4: 고객사별 알림 + 마무리
**Files:** `notify.py`(dispatch(extra_receivers)), `monitor.py`, `storage.receivers_for_device(dev)`, 알고리즘 내역, build_web, 아티팩트.
