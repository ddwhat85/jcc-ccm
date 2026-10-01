"""AI에게 물어보기 — 실제 기록만 보고 답하는 질의응답(Claude API 도구 호출).

원칙
  - AI는 설명만 한다. 도구는 전부 읽기 전용이고, 벤트·히터·팬을 움직이는 도구는 없다.
    출력 결정은 지금처럼 검증된 규칙(predict/튜닝 시험)이 한다.
  - 데이터가 외부(Anthropic)로 나간다 → 고객사별 스위치(accounts.ai_enabled, 기본 꺼짐).
    부르는 쪽(app)이 허용 판넬 집합을 계산해 넘기고, 모든 도구가 그 집합으로 거른다.
  - 지어내지 않게: 기록에 없는 것은 모른다고 말하게 지시하고, AI가 실제로 조회한 기록을
    근거 목록으로 돌려준다(답 본문의 [경보#12] 같은 표시와 짝).

의존성
  공식 SDK `anthropic`은 선택 의존성이다. 설치돼 있지 않거나 ANTHROPIC_API_KEY가 없으면
  이 기능만 꺼지고 서버의 나머지는 표준 라이브러리만으로 그대로 돈다.
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta, timezone

MODEL = os.environ.get("JCC_AI_MODEL", "claude-opus-5-5")
MONTHLY_LIMIT = int(os.environ.get("JCC_AI_MONTHLY_LIMIT", "200") or 200)   # 고객사별 월 질문 수(고객 계정·월간 해설)
# JCC 직원(관리자) 몫 — 점검 업무용이라 넉넉히. 한도는 비용 폭주를 막는 안전장치일 뿐이다.
STAFF_LIMIT = int(os.environ.get("JCC_AI_STAFF_LIMIT", "1000") or 1000)
MAX_ROUNDS = 6            # 도구 왕복 상한(한 질문이 끝없이 도는 것 방지)
MAX_TOKENS = 4000
MAX_QUESTION = 1000
MAX_HISTORY = 3           # 이어 묻기: 직전 질문·답 3쌍(텍스트만)
MAX_EVIDENCE = 40
_FALLBACK_BETA = "server-side-fallback-2026-07-01"
_KST = timezone(timedelta(hours=9))

USAGE_SCHEMA = """
CREATE TABLE IF NOT EXISTS ai_usage (
    customer_id INTEGER, period TEXT, questions INTEGER DEFAULT 0,
    input_tokens INTEGER DEFAULT 0, output_tokens INTEGER DEFAULT 0, cache_read_tokens INTEGER DEFAULT 0,
    PRIMARY KEY (customer_id, period));
"""

SYSTEM = """너는 JCC-CCM 판넬 감시 시스템의 설명 담당이다. 사용자는 배전반·제어반을 관리하는 현장 담당자다.

할 일
- 질문에 답하려면 먼저 도구로 실제 기록을 조회하라. 기억이나 일반론으로 현장 상태를 지어내지 마라.
- 도구 결과에 없는 사실은 "기록에 없다" 또는 "알 수 없다"고 말하라. 추측이면 추측이라고 밝혀라.
- 근거가 된 기록은 문장 끝에 도구 결과의 ref를 대괄호로 달아라. 예: 벤트가 열렸다 [기록#381].
- 시각은 한국 시간으로, "10월 1일 14:03"처럼 쓴다. 수치에는 단위를 붙인다.

알아 둘 시스템 동작
- 화재 징조는 FRI(0~100)와 단계(normal/watch/warning/danger/critical)로 판정하고, danger 이상이면 벤트를 자동으로 연다.
- 접점 발열은 전류로 예상한 온도와 실제 온도의 차이(residual, °C)로 판정한다. 기준 학습이 끝나야 서서히 풀리는 접점도 잡는다.
- 결로는 이슬점과 표면 온도의 여유(margin, °C)로 판정하고 히터·팬을 돌린다.
- 남은 여유 예측(remaining_life)은 하루 중앙값의 추세로 기준선까지 남은 날을 범위로 낸 것이다. 추정이므로 범위와 함께 말하라.
- 이 판정과 출력 결정은 검증된 규칙이 한다. 너는 그것을 설명할 뿐이고, 출력을 조작할 수 없다.
  조작을 요청받으면 대시보드에서 담당자가 직접 해야 한다고 안내하라.

답의 모양
- 한국어, 짧고 분명하게. 결론을 먼저, 근거를 뒤에. 필요하면 짧은 목록.
- 현장에서 지금 확인할 것이 있으면 마지막에 한 줄로 권한다."""

MONTHLY_SYSTEM = """너는 배전반 감시 서비스(JCC-CCM)의 월간 리포트 첫머리에 들어갈 요약을 쓴다. 읽는 사람은 고객사 설비 담당자와 관리자다.

규칙
- 주어진 리포트 JSON에 있는 사실과 숫자만 쓴다. 새 숫자를 계산하거나 만들지 마라(합계·비율·차이 금지). 리포트에 없는 숫자는 쓰지 않는다.
- 한국어 3~5문장, 문단 하나. 목록·제목·이모지 없이.
- 결과 언어로: 무엇을 먼저 잡았고, 무엇이 자동으로 처리됐고, 가동은 어땠는지. 장비 용어는 줄인다.
- 마지막 문장은 리포트의 advice 중 가장 중요한 것 하나를 권고로 쓴다. 특이사항이 없으면 지금 상태 유지를 권한다.
- 문제를 부풀리거나 줄이지 않는다."""

TOOLS = [
    {"name": "list_panels",
     "description": "볼 수 있는 판넬 전체 목록. 판넬 id·이름·온라인 여부, 화재/접점/결로 판정 단계, 활성 경보 수.",
     "input_schema": {"type": "object", "properties": {}, "additionalProperties": False}},
    {"name": "panel_status",
     "description": "판넬 하나의 지금 상태: 센서 최신값, 예지 판정 근거(FRI·접점 잔차·결로 여유), 벤트·히터·팬 상태와 현장 확인 결과.",
     "input_schema": {"type": "object", "properties": {
         "panel": {"type": "string", "description": "판넬 id(list_panels의 panel)"}},
         "required": ["panel"], "additionalProperties": False}},
    {"name": "alarm_history",
     "description": "기간 안에 발생한 경보(해제된 것 포함). 경보 id·종류·심각도·발생/확인/해제 시각·내용.",
     "input_schema": {"type": "object", "properties": {
         "panel": {"type": "string", "description": "판넬 id. 비우면 전체"},
         "days": {"type": "number", "description": "최근 며칠(1~90, 기본 7)"}},
         "additionalProperties": False}},
    {"name": "event_log",
     "description": "기간 안의 활동 기록: 벤트 자동 개방·닫힘, 히터·팬, 사람의 조작·경보 확인, 기준 학습, 설정 변경 등.",
     "input_schema": {"type": "object", "properties": {
         "panel": {"type": "string", "description": "판넬 id. 비우면 전체"},
         "days": {"type": "number", "description": "최근 며칠(1~90, 기본 7)"}},
         "additionalProperties": False}},
    {"name": "sensor_trend",
     "description": "센서 하나의 최근 추세: 최소·최대·평균·처음/마지막 값·시간당 변화, 고착·드리프트 여부, 24개로 줄인 값 목록.",
     "input_schema": {"type": "object", "properties": {
         "panel": {"type": "string"},
         "sensor_key": {"type": "string", "description": "panel_status의 센서 sensor_key"},
         "hours": {"type": "number", "description": "최근 몇 시간(1~336, 기본 24)"}},
         "required": ["panel", "sensor_key"], "additionalProperties": False}},
    {"name": "remaining_life",
     "description": "판넬의 남은 여유 예측: 하루 대표값 추세로 접점 잔차·센서 기준선이 위험/경고 기준에 닿기까지 남은 날(범위). 데이터 7일 미만이면 예측 없음.",
     "input_schema": {"type": "object", "properties": {
         "panel": {"type": "string"}}, "required": ["panel"], "additionalProperties": False}},
]


def _kst(ts) -> str:
    if not ts:
        return ""
    return datetime.fromtimestamp(ts, _KST).strftime("%m-%d %H:%M")


def period_of(ts: float | None = None) -> str:
    return datetime.fromtimestamp(time.time() if ts is None else ts, _KST).strftime("%Y-%m")


def _num(v, lo, hi, default):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, v))


def default_client():
    """API 키가 있고 SDK가 설치돼 있으면 클라이언트, 아니면 (None, 이유)."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None, "서버에 ANTHROPIC_API_KEY가 설정되지 않았습니다"
    try:
        import anthropic
    except ImportError:
        return None, "서버에 anthropic 패키지가 설치되지 않았습니다"
    return anthropic.Anthropic(timeout=90.0, max_retries=2), ""


def numbers_grounded(text: str, source: str) -> bool:
    """글에 나온 숫자가 모두 원문(리포트 JSON)에 있나. 'AI가 숫자를 지어내지 않았다'는 최소 확인."""
    import re
    def norm(n: str) -> str:              # 09 → 9, 12.50 → 12.5, 3.0 → 3
        return n.rstrip("0").rstrip(".") if "." in n else str(int(n))
    have = {norm(n) for n in re.findall(r"\d+(?:\.\d+)?", source)}
    return all(norm(n) in have for n in re.findall(r"\d+(?:\.\d+)?", text.replace(",", "")))


class AIError(Exception):
    """사람에게 보여 줄 수 있는 실패(상태 코드 포함)."""

    def __init__(self, msg: str, status: int = 502):
        super().__init__(msg)
        self.status = status


class Assistant:
    def __init__(self, storage, client=None, reason: str = ""):
        self.storage = storage
        self.client = client
        self.reason = reason if client is None else ""
        with storage._lock:
            storage._conn.executescript(USAGE_SCHEMA)
            storage._conn.commit()

    @property
    def available(self) -> bool:
        return self.client is not None

    # ── 사용량·한도 ─────────────────────────────────────────
    def usage(self, customer_id: int, period: str | None = None) -> dict:
        period = period or period_of()
        with self.storage._lock:
            r = self.storage._conn.execute(
                "SELECT questions, input_tokens, output_tokens, cache_read_tokens FROM ai_usage "
                "WHERE customer_id=? AND period=?", (customer_id, period)).fetchone()
        d = dict(r) if r else {"questions": 0, "input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0}
        lim = STAFF_LIMIT if customer_id == 0 else MONTHLY_LIMIT
        return dict(d, period=period, limit=lim, remaining=max(0, lim - d["questions"]))

    def _record(self, customer_id: int, u: dict) -> None:
        with self.storage._lock:
            self.storage._conn.execute(
                "INSERT INTO ai_usage (customer_id, period, questions, input_tokens, output_tokens, cache_read_tokens) "
                "VALUES (?, ?, 1, ?, ?, ?) ON CONFLICT(customer_id, period) DO UPDATE SET "
                "questions=questions+1, input_tokens=input_tokens+excluded.input_tokens, "
                "output_tokens=output_tokens+excluded.output_tokens, "
                "cache_read_tokens=cache_read_tokens+excluded.cache_read_tokens",
                (customer_id, period_of(), u["input_tokens"], u["output_tokens"], u["cache_read_tokens"]))
            self.storage._conn.commit()

    # ── 질문 ───────────────────────────────────────────────
    def ask(self, question: str, allowed_panels: set, customer_id: int = 0, history=None) -> dict:
        """allowed_panels: AI에 보내도 되는 판넬 id 집합(부르는 쪽이 계정 범위·고객사 스위치로 계산)."""
        if not self.available:
            raise AIError(self.reason or "AI 기능이 꺼져 있습니다", 503)
        question = (question or "").strip()
        if not question:
            raise AIError("질문을 입력하세요", 400)
        if len(question) > MAX_QUESTION:
            raise AIError(f"질문은 {MAX_QUESTION}자 이내로 써 주세요", 400)
        u = self.usage(customer_id)
        if u["remaining"] <= 0:
            raise AIError(f"이번 달 AI 질문 한도({u['limit']}회)를 다 썼습니다", 429)

        tools = _Tools(self.storage, set(allowed_panels))
        messages = []
        pairs = [h for h in (history or [])
                 if isinstance(h, dict) and str(h.get("q", "")).strip() and str(h.get("a", "")).strip()]
        for h in pairs[-MAX_HISTORY:]:
            messages.append({"role": "user", "content": str(h["q"])[:MAX_QUESTION]})
            messages.append({"role": "assistant", "content": str(h["a"])[:4000]})
        messages.append({"role": "user", "content": question})

        used = {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0}
        answer, note, model = "", "", MODEL
        try:
            for _ in range(MAX_ROUNDS):
                resp = self._call(messages)
                model = getattr(resp, "model", model) or model
                u = getattr(resp, "usage", None)
                if u is not None:
                    used["input_tokens"] += int(getattr(u, "input_tokens", 0) or 0) + \
                        int(getattr(u, "cache_creation_input_tokens", 0) or 0)
                    used["output_tokens"] += int(getattr(u, "output_tokens", 0) or 0)
                    used["cache_read_tokens"] += int(getattr(u, "cache_read_input_tokens", 0) or 0)
                stop = getattr(resp, "stop_reason", None)
                if stop == "refusal":       # 대체 모델까지 거절 — 내용은 읽지 않는다
                    answer, note = "", "이 질문에는 답할 수 없습니다. 판넬 상태나 기록에 관해 물어봐 주세요."
                    break
                content = [b for b in resp.content if getattr(b, "type", "") != "fallback"]
                text = "".join(getattr(b, "text", "") for b in content if getattr(b, "type", "") == "text").strip()
                calls = [b for b in content if getattr(b, "type", "") == "tool_use"]
                if stop == "tool_use" and calls:
                    messages.append({"role": "assistant", "content": content})
                    messages.append({"role": "user", "content": [tools.run(b) for b in calls]})
                    continue
                if stop == "pause_turn":
                    messages.append({"role": "assistant", "content": content})
                    continue
                answer = text
                if stop == "max_tokens":
                    note = "답이 길어 중간에 끊겼습니다. 범위를 좁혀 다시 물어봐 주세요."
                break
            else:
                note = "기록을 너무 많이 찾아봐야 하는 질문이라 여기서 멈췄습니다. 판넬이나 기간을 좁혀 주세요."
        finally:
            if used["input_tokens"] or used["output_tokens"]:
                self._record(customer_id, used)

        cited = set()
        for ev in tools.evidence:
            if f"[{ev['ref']}]" in answer:
                ev["cited"] = True
                cited.add(ev["ref"])
        evidence = sorted(tools.evidence, key=lambda e: (not e.get("cited"), -(e.get("ts") or 0)))[:MAX_EVIDENCE]
        return {"answer": answer, "note": note, "evidence": evidence, "model": model, "usage": used,
                "remaining": self.usage(customer_id)["remaining"]}

    def summarize_monthly(self, rep: dict, customer_id: int) -> dict | None:
        """월간 리포트 첫머리 요약(3~5문장). 리포트에 없는 숫자가 나오면 버린다(None). 실패해도 None —
        리포트 발행을 막지 않는다. 사용량은 그 고객사 몫으로 1회."""
        if not self.available or self.usage(customer_id)["remaining"] <= 0:
            return None
        data = {k: rep.get(k) for k in ("customer", "period", "summary", "panels", "problems", "life", "advice")}
        body = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        try:
            resp = self._call([{"role": "user", "content": f"리포트 JSON:\n{body}\n\n요약을 써 줘."}],
                              system=MONTHLY_SYSTEM, tools=None, max_tokens=1500)
        except AIError:
            return None
        u = getattr(resp, "usage", None)
        self._record(customer_id, {
            "input_tokens": int(getattr(u, "input_tokens", 0) or 0) + int(getattr(u, "cache_creation_input_tokens", 0) or 0),
            "output_tokens": int(getattr(u, "output_tokens", 0) or 0),
            "cache_read_tokens": int(getattr(u, "cache_read_input_tokens", 0) or 0)})
        if getattr(resp, "stop_reason", None) != "end_turn":
            return None
        text = "".join(getattr(b, "text", "") for b in resp.content if getattr(b, "type", "") == "text").strip()
        if not text or not numbers_grounded(text, body):
            return None
        return {"text": text[:1200], "model": getattr(resp, "model", MODEL) or MODEL, "at": time.time()}

    def _call(self, messages, system: str = SYSTEM, tools=TOOLS, max_tokens: int = MAX_TOKENS):
        kwargs = dict(
            model=MODEL,
            max_tokens=max_tokens,
            # 지시·도구는 매번 같다 → 캐시해 두 번째 왕복부터 싸게(도구 → 지시 순으로 앞부분이 고정)
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            messages=messages,
            output_config={"effort": "medium"},
            betas=[_FALLBACK_BETA],
            fallbacks="default",          # 안전 분류기가 거절하면 서버가 권장 모델로 다시 돌린다
        )
        if tools:
            kwargs["tools"] = tools
        try:
            import anthropic
        except ImportError:               # 시험용 가짜 클라이언트(SDK 없는 환경)
            return self.client.beta.messages.create(**kwargs)
        try:
            return self.client.beta.messages.create(**kwargs)
        except anthropic.AuthenticationError:
            raise AIError("AI 서비스 키가 올바르지 않습니다(관리자에게 알려 주세요)", 503)
        except anthropic.RateLimitError:
            raise AIError("AI 서비스가 잠시 붐빕니다. 1분 뒤 다시 물어봐 주세요", 503)
        except anthropic.BadRequestError as e:
            raise AIError(f"AI 요청이 거절됐습니다: {getattr(e, 'message', e)}", 502)
        except anthropic.APIStatusError as e:
            raise AIError(f"AI 서비스 오류({e.status_code}). 잠시 뒤 다시 시도해 주세요", 502)
        except anthropic.APIConnectionError:
            raise AIError("AI 서비스에 연결하지 못했습니다", 502)


class _Tools:
    """읽기 전용 조회 도구. 모든 결과는 허용 판넬로 거르고, 조회한 기록을 근거로 모은다."""

    def __init__(self, storage, allowed: set):
        self.st = storage
        self.allowed = allowed
        self.evidence: list = []
        self._seen: set = set()
        self._panels = None

    # 판넬 → 소속 기기, 판넬 이름
    def panels(self) -> list:
        if self._panels is None:
            self._panels = [p for p in self.st.list_panels() if p["panel"] in self.allowed]
        return self._panels

    def _panel(self, pid):
        for p in self.panels():
            if p["panel"] == pid:
                return p
        return None

    def _keys(self, pid=None) -> set:
        """기록 조회용 기기·판넬 키(허용 범위 안)."""
        ps = self.panels() if not pid else [p for p in self.panels() if p["panel"] == pid]
        keys = set()
        for p in ps:
            keys.add(p["panel"])
            keys.update(c["device_id"] for c in p["ccms"])
        return keys

    def _owner(self) -> dict:
        """기기·판넬 키 → 판넬 id."""
        m = {}
        for p in self.panels():
            m[p["panel"]] = p["panel"]
            for c in p["ccms"]:
                m[c["device_id"]] = p["panel"]
        return m

    def _cite(self, ref, kind, label, ts=None, panel=""):
        if ref in self._seen:
            return
        self._seen.add(ref)
        self.evidence.append({"ref": ref, "kind": kind, "label": label[:200], "ts": ts, "panel": panel})

    def run(self, block) -> dict:
        name, args = getattr(block, "name", ""), getattr(block, "input", None) or {}
        fn = getattr(self, "t_" + name, None)
        try:
            if fn is None or not isinstance(args, dict):
                raise ValueError(f"없는 도구입니다: {name}")
            out = fn(**args)
            return {"type": "tool_result", "tool_use_id": block.id,
                    "content": json.dumps(out, ensure_ascii=False, separators=(",", ":"))}
        except (ValueError, TypeError) as exc:
            return {"type": "tool_result", "tool_use_id": block.id, "content": f"오류: {exc}", "is_error": True}

    def _need_panel(self, panel):
        p = self._panel(str(panel or ""))
        if p is None:
            raise ValueError(f"볼 수 없거나 없는 판넬입니다: {panel}. list_panels로 확인하세요")
        return p

    # ── 도구 ───────────────────────────────────────────────
    def t_list_panels(self):
        pred = {r.get("panel"): r for r in self.st.predict_state()}
        keys_of = {p["panel"]: self._keys(p["panel"]) for p in self.panels()}
        active = self.st.list_active_alarms()
        out = []
        for p in self.panels():
            r = pred.get(p["panel"]) or {}
            out.append({
                "panel": p["panel"], "name": p["panel_name"], "online": p["online"], "ccm_count": p["ccm_count"],
                "fire": (r.get("fire") or {}).get("stage"), "fri": (r.get("fire") or {}).get("fri"),
                "contact": (r.get("contact") or {}).get("stage"), "dew": (r.get("dew") or {}).get("stage"),
                "active_alarms": sum(1 for a in active if a.get("device_id") in keys_of[p["panel"]]),
            })
        return {"panels": out, "now": _kst(time.time())}

    def t_panel_status(self, panel):
        p = self._need_panel(panel)
        sensors = []
        now = time.time()
        for c in p["ccms"]:
            for s in c.get("latest") or []:
                if s.get("enabled") is False:
                    continue
                sensors.append({"device": c["device_id"], "sensor_key": s["sensor_key"], "name": s.get("name"),
                                "kind": s.get("kind"), "value": s.get("value"), "unit": s.get("unit"),
                                "ok": bool(s.get("ok")),
                                "age_s": None if not s.get("ts") else round(now - s["ts"]),
                                "alarm_warn": s.get("alarm_warn"), "alarm_max": s.get("alarm_max")})
        r = next((x for x in self.st.predict_state() if x.get("panel") == p["panel"]), None)
        pred = None
        if r:
            f, c, d = r.get("fire") or {}, r.get("contact") or {}, r.get("dew") or {}
            pred = {
                "judged_at": _kst(r.get("ts")),
                "fire": {k: f.get(k) for k in ("stage", "fri", "reasons", "vent")},
                "contact": {k: c.get(k) for k in ("stage", "residual", "residual_slope", "expected", "delta_t",
                                                  "reasons", "baseline")},
                "dew": {k: d.get(k) for k in ("stage", "dew_point", "margin", "margin_slope", "rh", "heater", "fan",
                                              "reasons")},
                "field": {dev: {k: e.get(k) for k in ("roles", "confirm", "position", "fault")}
                          for dev, e in (r.get("edge") or {}).items()},
            }
        ref = f"상태:{p['panel']}"
        self._cite(ref, "status", f"{p['panel_name']} 지금 상태", now, p["panel"])
        return {"ref": ref, "panel": p["panel"], "name": p["panel_name"], "online": p["online"],
                "sensors": sensors, "predict": pred}

    def t_alarm_history(self, panel="", days=7):
        if panel:
            self._need_panel(panel)
        days = _num(days, 1, 90, 7)
        owner = self._owner()
        rows = self.st.alarms_since(time.time() - days * 86400, self._keys(panel or None), 100)
        out = []
        for a in rows:
            ref = f"경보#{a['id']}"
            pid = owner.get(a["device_id"], "")
            self._cite(ref, "alarm", f"{a['kind']} — {a.get('detail') or ''}", a["raised_at"], pid)
            out.append({"ref": ref, "panel": pid, "device": a["device_id"], "sensor": a.get("sensor_key"),
                        "kind": a["kind"], "severity": a.get("severity"), "detail": a.get("detail"),
                        "raised": _kst(a["raised_at"]), "acked": _kst(a.get("acked_at")), "acked_by": a.get("acked_by"),
                        "escalated": _kst(a.get("escalated_at")), "cleared": _kst(a.get("cleared_at")) or "아직 열림"})
        return {"days": days, "count": len(out), "alarms": out}

    def t_event_log(self, panel="", days=7):
        if panel:
            self._need_panel(panel)
        days = _num(days, 1, 90, 7)
        owner = self._owner()
        rows = self.st.events_since(time.time() - days * 86400, self._keys(panel or None), 120)
        out = []
        for e in rows:
            ref = f"기록#{e['id']}"
            pid = owner.get(e["device_id"], "")
            self._cite(ref, "event", e.get("detail") or e["etype"], e["ts"], pid)
            out.append({"ref": ref, "at": _kst(e["ts"]), "panel": pid, "type": e["etype"],
                        "detail": e.get("detail"), "by": e.get("source")})
        return {"days": days, "count": len(out), "events": out}

    def t_remaining_life(self, panel):
        p = self._need_panel(panel)
        from .rul import panel_view
        pv = panel_view(self.st, {p["panel"]})
        items = pv[0]["items"] if pv else []
        ref = f"여유:{p['panel']}"
        self._cite(ref, "life", f"{p['panel_name']} 남은 여유 예측", time.time(), p["panel"])
        return {"ref": ref, "items": [{k: i.get(k) for k in ("label", "status", "say", "advice", "days", "days_lo",
                                                              "days_hi", "current", "threshold", "unit", "n_days",
                                                              "slope_per_day")} for i in items]}

    def t_sensor_trend(self, panel, sensor_key, hours=24):
        p = self._need_panel(panel)
        hours = _num(hours, 1, 336, 24)
        dev = next((c["device_id"] for c in p["ccms"]
                    for s in c.get("latest") or [] if s.get("sensor_key") == sensor_key), None)
        if dev is None:
            raise ValueError(f"이 판넬에 없는 센서입니다: {sensor_key}. panel_status로 확인하세요")
        since = time.time() - hours * 3600
        pts = [x for x in self.st.history(dev, sensor_key, 5000)
               if x["ts"] >= since and x.get("ok") and x.get("value") is not None]
        ref = f"추세:{p['panel']}/{sensor_key}"
        self._cite(ref, "trend", f"{p['panel_name']} {sensor_key} 최근 {hours:g}시간", time.time(), p["panel"])
        if not pts:
            return {"ref": ref, "points": 0, "note": "이 기간에 정상 측정값이 없습니다"}
        vals = [x["value"] for x in pts]
        span_h = max((pts[-1]["ts"] - pts[0]["ts"]) / 3600, 1e-6)
        step = max(1, len(pts) // 24)
        hs = self.st.history_stats(dev, sensor_key, 60)
        return {"ref": ref, "points": len(pts), "from": _kst(pts[0]["ts"]), "to": _kst(pts[-1]["ts"]),
                "min": round(min(vals), 3), "max": round(max(vals), 3), "mean": round(sum(vals) / len(vals), 3),
                "first": round(vals[0], 3), "last": round(vals[-1], 3),
                "change_per_hour": round((vals[-1] - vals[0]) / span_h, 4),
                "stuck": hs.get("stuck"), "drift": hs.get("drift"), "drift_pct": hs.get("drift_pct"),
                "sampled": [[_kst(x["ts"]), round(x["value"], 3)] for x in pts[::step]][-24:]}
