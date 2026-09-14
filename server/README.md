# JCC-CCM 서버

CCM 펌웨어가 보낸 텔레메트리를 받아 저장하고, 조회 API와 대시보드를 제공한다.
**파이썬 표준 라이브러리만** 사용 — pip 설치 불필요, 어떤 리눅스 서버에서도 그대로 실행.

> FastAPI를 검토했으나 이 개발 PC의 Windows Smart App Control이 pydantic_core
> 네이티브 DLL을 차단해 실행 불가. 표준 라이브러리 구현이 오히려 의존성 0·이식성·
> 소유권 면에서 이 제품에 유리하다고 판단해 채택. 규모가 커지면 리눅스 서버에서
> FastAPI로 이전 가능(엔드포인트 형식 동일).

## 실행

```bash
cd server
python -m jcc_server --host 0.0.0.0 --port 8000 --db jcc.db
# 대시보드: http://localhost:8000/
```

환경변수 `JCC_API_KEY`를 설정하면 수집 POST에 `Authorization: Bearer <키>`를 요구한다.

## 엔드포인트

| 메서드 | 경로 | 용도 |
|---|---|---|
| POST | `/v1/telemetry` | 펌웨어 수집 데이터 (http_transport와 짝) |
| GET | `/api/devices` | 장비 목록 + 센서별 최신값 + 온라인 상태 |
| GET | `/api/devices/{id}/history?sensor=KEY&limit=N` | 센서 이력(시계열) |
| GET | `/health` | 상태 확인 |
| GET | `/` | 대시보드 |

## 구조

```
jcc_server/
  storage.py    SQLite 저장 (DB 접근을 여기 가둠 → 나중에 PostgreSQL 이전 쉬움)
  app.py        http.server 기반 라우터 + 엔드포인트 (ThreadingHTTPServer)
  __main__.py   실행 진입점
static/
  index.html    대시보드 (외부 CDN 없음, 차트 직접 렌더 → 오프라인 현장에서도 동작)
tools/
  demo_feed.py  실기 없이 서버·저장·대시보드 전 구간을 검증하는 데모 피더
```

## 로컬 데모 (실기 없이 전 구간 확인)

```bash
# 터미널 1: 서버
python -m jcc_server --port 8770 --db jcc.db
# 터미널 2: 가짜 CCM 2대가 실제 HTTP로 데이터 전송
python tools/demo_feed.py --url http://127.0.0.1:8770/v1/telemetry --devices 2 --interval 3
# 브라우저로 http://127.0.0.1:8770/ 접속 → 실시간 타일 + 센서 클릭 시 이력 차트
```

## 대시보드 특징

- 5초 폴링하되 **구성이 바뀔 때만 DOM 재생성**, 평소엔 값만 제자리 갱신(무깜빡임)
- 센서 성격별 경보색(수소·온도 임계 초과 시 강조) — 현장 임계는 추후 서버 설정으로
- 외부 라이브러리 0개: 캔버스로 이력 차트 직접 렌더
