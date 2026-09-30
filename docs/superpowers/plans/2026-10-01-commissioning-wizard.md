# 현장 설치 점검 마법사 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 판넬 하나를 연결·센서·예지 역할·출력 시험 순서로 점검하고 시운전 기록을 남긴다.

**Architecture:** 판정은 새 모듈 `commission.py`(순수에 가깝게 storage를 인자로)가 맡고, 출력 시험 상태기계도 거기 둔다.
app.py는 4개 경로(권한 표 등록)만 연결. CCM은 엣지 보고에 `roles`만 추가. 화면은 설치 점검 오버레이(admin)와
설치 보고서 보기(모두).

## Global Constraints
- 기존 diagnose 재사용, 설치 때 무의미한 '이상탐지'(평소값 학습 전)는 제외.
- 출력 시험은 끝나면 항상 자동 복귀 명령. 최대 150초.
- 새 경로는 반드시 ROUTES에 분류(test_scope 커버리지 통과).
- CSS 접두사 `cm-`. 커밋 끝 `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`

### Task 1: commission.py + 저장 + API + CCM roles
**Files:** Create `server/jcc_server/commission.py`, `server/tests/test_commission.py`; Modify `storage.py`(commission_reports, `ctests`),
`app.py`(ROUTES·4경로), `firmware/jcc_ccm/edge.py`(_report roles)
**Produces:** `check_panel(storage, panel, now=None) -> {panel, panel_name, ts, overall, steps:[{key,title,status,items:[{target,status,detail,advice}]}]}`,
`start_output_test(storage, panel, kind, by) -> str|None(오류)`, `advance_tests(storage, panel, now)`,
`Storage.add_commission_report(panel, by, overall, report) -> id`, `Storage.list_commission_reports(panels=None) -> list`.

### Task 2: 화면·데모·마무리
**Files:** `server/static/index.html`(cm- 오버레이·메뉴), `web/demo-api.js`(데모 판정·기록), 알고리즘 내역, build_web, 아티팩트.
