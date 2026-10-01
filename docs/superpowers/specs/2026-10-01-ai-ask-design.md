# AI에게 물어보기 — 설계

## 목적

대시보드에서 "3번 판넬 왜 벤트 열렸어?", "지난주 문제 있던 장비 정리해줘"처럼 물으면
실제 기록만 보고 답한다. 답마다 AI가 살펴본 기록(경보·활동 기록·센서 추세)을 근거로 함께 보여 준다.

## 원칙

- **AI는 설명만 한다.** 도구는 전부 읽기 전용. 벤트·히터·팬을 움직이는 도구는 없다.
  화재·결로 판정과 출력 결정은 지금처럼 검증된 규칙(튜닝 시험 통과)이 한다.
- **데이터가 외부(Anthropic)로 나간다** → 고객사별 스위치 `ai_enabled`(기본 꺼짐, JCC 관리자만 켬).
  - 고객 계정: 자기 고객사가 꺼져 있으면 기능 자체가 없다(403).
  - JCC 관리자: AI가 켜진 고객사 판넬 + 아직 배정 안 된(JCC 자체) 판넬만 AI에 보낸다.
    꺼진 고객사 판넬은 관리자가 물어도 보내지 않는다.
- **지어내지 않게**: 시스템 지시로 "도구로 받은 기록에 없는 것은 모른다고 말하라", 답 본문에
  근거 표시(예: `[경보#12]`)를 달게 한다. 화면은 AI가 실제로 조회한 기록 목록을 보여 주고,
  답에 인용된 것을 강조한다.
- **경보 문자는 AI를 기다리지 않는다.** 경보 발송 경로는 손대지 않는다. 대신 대시보드 경보
  옆 "AI 해설" 버튼이 그 경보를 물어보기 창으로 넘긴다(설계 변경: 문자에 AI 해설을 붙이면
  발송이 AI 응답에 묶이고, 실제 문자가 나가는 경로를 바꾸게 된다).

## 구성

| 단위 | 하는 일 |
|---|---|
| `server/jcc_server/ai.py` | `Assistant.ask()` — Claude API 도구 호출 루프, 도구 5개, 근거 수집, 사용량 기록·한도 |
| `accounts.py` | `customers.ai_enabled` 열, `set_ai()`, `list_customers()`에 포함 |
| `storage.py` | `alarms_since()`, `events_since()` — 기간 조회(범위 필터) |
| `app.py` | `GET /api/ai/status`, `POST /api/ai/ask`(둘 다 `read`), 관리 작업 `ai`(admin) |
| `index.html` | 물어보기 창(`aiq-`), 경보 "AI 해설" 버튼, 계정 관리의 고객사별 AI 스위치 |
| `web/demo-api.js` | 서버 없는 데모: 시연 데이터로 만든 예시 답(“데모 답변” 표시) |

### 도구 (읽기 전용, 허용 판넬로 거름)

1. `list_panels` — 판넬 목록, 온라인, 화재/접점/결로 단계, 활성 경보 수
2. `panel_status(panel)` — 센서 최신값, 예지 판정 근거, 현장 출력 상태(확인·고장)
3. `alarm_history(panel?, days)` — 기간 내 발생 경보(해제된 것 포함)
4. `event_log(panel?, days)` — 기간 내 활동 기록(자동 조치·사람 조작)
5. `sensor_trend(panel, sensor_key, hours)` — 최소·최대·평균·시간당 변화·고착/드리프트

### 호출

- 공식 SDK `anthropic`(선택 의존성 — 없거나 `ANTHROPIC_API_KEY`가 없으면 AI만 꺼지고 서버는 그대로)
- 모델 `claude-opus-5-5`(`JCC_AI_MODEL`로 변경 가능), effort `medium`, `max_tokens` 4000
- 서버 측 대체 모델 `fallbacks: "default"`(베타 `server-side-fallback-2026-07-01`)
- 시스템 지시에 캐시 표시(`cache_control`) — 도구·지시 앞부분이 매번 같아 재사용
- 도구 왕복 최대 6회, 질문 1,000자, 이어 묻기는 직전 3쌍(텍스트만)
- 거절(`refusal`)·한도 초과(`max_tokens`)는 사람이 읽을 문장으로 돌려준다

### 비용 한도

- 표 `ai_usage(customer_id, period, questions, input_tokens, output_tokens, cache_read_tokens)` —
  JCC 관리자 질문은 customer_id 0
- 고객사(및 JCC)별 월 질문 수 한도 `JCC_AI_MONTHLY_LIMIT`(기본 200). 넘으면 429와 안내
- 상태 API가 이번 달 사용량·한도를 돌려준다

## 시험 (실제 API 호출 없음 — 가짜 클라이언트 주입)

- 도구 루프: 도구 호출 → 결과 → 최종 답, 근거 수집·인용 표시
- 범위: 고객은 자기 판넬만, 관리자는 꺼진 고객사 판넬을 못 봄, 범위 밖 판넬 요청은 오류 결과
- 스위치 꺼짐 403, 키 없음 → 상태 `available: false`
- 월 한도 429, 사용량 누적
- 거절·max_tokens·API 오류 처리
- 도구 목록에 출력(액추에이터) 조작 도구가 없음
- ROUTES에 새 경로 분류(test_scope)
