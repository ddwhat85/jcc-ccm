"""AI에게 물어보기 — 도구 루프·근거·범위·고객사 스위치·월 한도. 실제 API는 부르지 않는다(가짜 클라이언트).

python -m tests.test_ai   (server/ 에서)
"""
from __future__ import annotations

import http.cookiejar
import json
import os
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from types import SimpleNamespace as NS

os.environ["JCC_DASHBOARD_PW"] = "env-admin-pw"      # app import 전에
os.environ["JCC_DASHBOARD_USER"] = "jccops"
os.environ.pop("JCC_API_KEY", None)
os.environ.pop("ANTHROPIC_API_KEY", None)
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from jcc_server import ai as aimod
from jcc_server import app as appmod
from jcc_server.predict import Predictor
from jcc_server.storage import Storage

_fails = []


def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not cond:
        _fails.append(name)


def resp(content, stop, out=40):
    return NS(content=content, stop_reason=stop, model="claude-opus-5-5",
              usage=NS(input_tokens=500, output_tokens=out, cache_read_input_tokens=300, cache_creation_input_tokens=0))


def tool(i, name, **inp):
    return NS(type="tool_use", id=f"tu{i}", name=name, input=inp)


def text(t):
    return NS(type="text", text=t)


class FakeClient:
    """script(call_no, kwargs) -> 응답. 받은 요청을 calls에 남긴다."""

    def __init__(self, script):
        self.script, self.calls = script, []
        self.beta = NS(messages=NS(create=self._create))

    def _create(self, **kw):
        kw = dict(kw, messages=list(kw["messages"]))
        self.calls.append(kw)
        return self.script(len(self.calls), kw)


def tool_results(kw) -> list:
    """마지막 user 메시지의 tool_result들 → [(내용 dict 또는 str, is_error)]."""
    out = []
    for b in kw["messages"][-1]["content"]:
        c = b["content"]
        try:
            c = json.loads(c)
        except ValueError:
            pass
        out.append((c, bool(b.get("is_error"))))
    return out


def setup():
    fd, db = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    st = Storage(db)
    st.predictor = Predictor()
    ac = st.accounts
    ca, cb = ac.create_customer("A 제조"), ac.create_customer("B 물류")
    ac.assign_panel("panel-A", ca)
    ac.assign_panel("panel-B", cb)
    now = time.time()
    for dev, panel in (("ccm-a1", "panel-A"), ("ccm-b1", "panel-B"), ("ccm-x1", "panel-X")):   # X = 미배정
        for k in range(30):
            st.ingest({"device_id": dev, "panel": panel, "panel_name": panel, "readings": [
                {"key": "h2_lel", "name": "수소", "unit": "%LEL", "value": 0.5 + k * 0.01, "ok": True,
                 "ts": now - (30 - k) * 60}]})
        st.raise_alarm(dev, "h2_lel", "alarm", f"{dev} 수소 경보")
        st.log_event(dev, "", "vent_open", f"{dev} 벤트 자동 개방", source="system")
    return st, db, ca, cb


def run():
    st, db, ca, cb = setup()
    every = {"panel-A", "panel-B", "panel-X"}

    print("=== 도구 루프·근거 (모듈 직접) ===")

    def script(n, kw):
        if n == 1:
            return resp([text("기록을 보겠습니다."), tool(1, "list_panels"), tool(2, "alarm_history", days=3)], "tool_use")
        alarms = tool_results(kw)[1][0]["alarms"]
        ref = next(a["ref"] for a in alarms if a["panel"] == "panel-A")
        return resp([text(f"panel-A에서 수소 경보가 났습니다 [{ref}].")], "end_turn")
    fake = FakeClient(script)
    a = aimod.Assistant(st, fake)
    r = a.ask("최근 경보 정리해줘", every, customer_id=0)
    check("두 번 왕복 후 답", len(fake.calls) == 2 and "수소 경보" in r["answer"], r["answer"])
    first = fake.calls[0]
    check("모델·대체·노력·캐시 설정", first["model"] == "claude-opus-5-5" and first["fallbacks"] == "default"
          and first["betas"] == ["server-side-fallback-2026-07-01"] and first["output_config"] == {"effort": "medium"}
          and first["system"][0].get("cache_control") == {"type": "ephemeral"})
    names = {t["name"] for t in first["tools"]}
    check("도구 6개, 모두 조회용", names == {"list_panels", "panel_status", "alarm_history", "event_log", "sensor_trend",
                                       "remaining_life"},
          str(names))
    check("출력 조작 도구 없음", not any(n.startswith(w) for n in names for w in ("set", "open", "close", "ack", "run", "write"))
          and not any(w in n for n in names for w in ("actuat", "command", "control")))
    second = fake.calls[1]["messages"]
    check("도구 호출 → 결과 순서(어시스턴트 → user tool_result)",
          second[-2]["role"] == "assistant" and second[-1]["content"][0]["type"] == "tool_result"
          and second[-1]["content"][0]["tool_use_id"] == "tu1")
    cited = [e for e in r["evidence"] if e.get("cited")]
    check("인용된 근거 표시·맨 앞", len(cited) == 1 and r["evidence"][0].get("cited") and cited[0]["kind"] == "alarm",
          str(r["evidence"][:2]))
    check("조회한 기록이 근거 목록에(경보 3건)", sum(1 for e in r["evidence"] if e["kind"] == "alarm") == 3)
    u = a.usage(0)
    check("사용량 누적(질문 1, 토큰)", u["questions"] == 1 and u["input_tokens"] == 1000 and u["output_tokens"] == 80
          and u["cache_read_tokens"] == 600, str(u))

    print("\n=== 범위: 허용 판넬 밖은 도구가 거른다 ===")

    def script2(n, kw):
        if n == 1:
            return resp([tool(1, "list_panels"), tool(2, "panel_status", panel="panel-B"),
                         tool(3, "event_log", days=7), tool(4, "sensor_trend", panel="panel-A", sensor_key="h2_lel")],
                        "tool_use")
        res = tool_results(kw)
        script2.res = res
        return resp([text("panel-A만 보입니다.")], "end_turn")
    a2 = aimod.Assistant(st, FakeClient(script2))
    a2.ask("전체 상태", {"panel-A"}, customer_id=ca)
    res = script2.res
    check("판넬 목록: A만", [p["panel"] for p in res[0][0]["panels"]] == ["panel-A"], str(res[0][0]))
    check("범위 밖 판넬 → 오류 결과", res[1][1] and "볼 수 없거나" in res[1][0])
    check("기록: A 것만", res[2][0]["count"] >= 1 and all(e["panel"] == "panel-A" for e in res[2][0]["events"]))
    tr = res[3][0]
    check("센서 추세 요약", tr["points"] == 30 and tr["min"] < tr["max"] and tr["change_per_hour"] > 0
          and len(tr["sampled"]) <= 24, str({k: tr[k] for k in ("points", "min", "max", "change_per_hour")}))
    check("고객 사용량은 고객사 몫으로", a2.usage(ca)["questions"] == 1 and a2.usage(0)["questions"] == 1)

    print("\n=== 거절·끊김·왕복 상한 ===")
    a3 = aimod.Assistant(st, FakeClient(lambda n, kw: resp([], "refusal")))
    r = a3.ask("아무거나", every)
    check("거절: 빈 답 + 안내", r["answer"] == "" and "답할 수 없습니다" in r["note"])
    a3 = aimod.Assistant(st, FakeClient(lambda n, kw: resp([text("길게…")], "max_tokens")))
    r = a3.ask("아무거나", every)
    check("max_tokens: 받은 데까지 + 안내", r["answer"] == "길게…" and "끊겼습니다" in r["note"])
    loop = FakeClient(lambda n, kw: resp([tool(n, "list_panels")], "tool_use"))
    r = aimod.Assistant(st, loop).ask("끝없이", every)
    check(f"왕복 상한 {aimod.MAX_ROUNDS}회에서 멈춤", len(loop.calls) == aimod.MAX_ROUNDS and "멈췄습니다" in r["note"])
    fb = FakeClient(lambda n, kw: resp([NS(type="fallback"), text("대체 모델 답")], "end_turn"))
    r = aimod.Assistant(st, fb).ask("q", every)
    check("대체 모델 표시 블록은 걸러 답만", r["answer"] == "대체 모델 답")

    print("\n=== 이어 묻기·입력 검사 ===")
    hist_fake = FakeClient(lambda n, kw: resp([text("네")], "end_turn"))
    ah = aimod.Assistant(st, hist_fake)
    ah.ask("그럼 B는?", every, history=[{"q": f"질문{i}", "a": f"답{i}"} for i in range(5)] + [{"q": "", "a": "x"}])
    msgs = hist_fake.calls[0]["messages"]
    check("직전 3쌍만 + 이번 질문", len(msgs) == 7 and msgs[0]["content"] == "질문2" and msgs[-1]["content"] == "그럼 B는?",
          str([m["content"] for m in msgs]))
    for q, code in (("", 400), ("가" * (aimod.MAX_QUESTION + 1), 400)):
        try:
            ah.ask(q, every)
            check(f"잘못된 질문 {code}", False)
        except aimod.AIError as exc:
            check(f"잘못된 질문 → {code}", exc.status == code)
    off = aimod.Assistant(st, None, "서버에 ANTHROPIC_API_KEY가 설정되지 않았습니다")
    try:
        off.ask("q", every)
        check("키 없음 503", False)
    except aimod.AIError as exc:
        check("키 없음 → 503 + 이유", exc.status == 503 and "ANTHROPIC_API_KEY" in str(exc))
    c, why = aimod.default_client()
    check("기본 클라이언트: 키 없으면 꺼짐", c is None and "ANTHROPIC_API_KEY" in why)

    print("\n=== HTTP: 고객사 스위치·범위·한도 ===")
    st.ai = aimod.Assistant(st, FakeClient(lambda n, kw: resp([text("ok")], "end_turn")))
    ac = st.accounts
    mid, mtemp = ac.create_user("kim.a", "manager", ca)
    srv = appmod.make_server("127.0.0.1", 0, st)
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    def client():
        op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

        def call(path, body=None):
            req = urllib.request.Request(base + path, method="POST" if body is not None else "GET",
                                         data=None if body is None else json.dumps(body).encode(),
                                         headers={"Content-Type": "application/json"})
            try:
                with op.open(req, timeout=10) as r:
                    return r.status, json.loads(r.read().decode("utf-8") or "{}")
            except urllib.error.HTTPError as e:
                return e.code, json.loads(e.read().decode("utf-8") or "{}")
        return call

    try:
        check("새 경로가 권한 표에 있음", appmod.route_policy("GET", "/api/ai/status") == "read"
              and appmod.route_policy("POST", "/api/ai/ask") == "read"
              and appmod.route_policy("POST", "/api/admin/ai") == "admin")
        adm = client()
        adm("/api/login", {"user": "jccops", "password": "env-admin-pw"})
        m = client()
        m("/api/login", {"user": "kim.a", "password": mtemp})
        m("/api/me/password", {"old": mtemp, "new": "manager-pw-2026"})
        m("/api/login", {"user": "kim.a", "password": "manager-pw-2026"})

        s, j = m("/api/ai/status")
        check("고객: 기본 꺼짐 표시", s == 200 and j["enabled"] is False and "꺼져" in j["blocked"], str(j))
        s, j = m("/api/ai/ask", {"question": "상태?"})
        check("고객: 꺼져 있으면 403", s == 403)
        s, j = adm("/api/ai/status")
        check("관리자: 꺼진 고객사 판넬 2개 제외(미배정 X만 허용)", j["enabled"] and j["panels"] == 1
              and j["excluded_panels"] == 2, str(j))
        s, j = m("/api/admin/ai", {"customer_id": ca, "on": True})
        check("고객은 스위치를 못 켬", s == 403)
        s, j = adm("/api/admin/ai", {"customer_id": ca, "on": True})
        check("관리자가 A 켬", s == 200)
        s, j = adm("/api/admin/accounts")
        check("계정 관리에 스위치 상태", {c["id"]: c["ai_enabled"] for c in j["customers"]} == {ca: True, cb: False})
        s, j = adm("/api/ai/status")
        check("관리자: 이제 A+X(B만 제외)", j["panels"] == 2 and j["excluded_panels"] == 1, str(j))
        s, j = m("/api/ai/status")
        check("고객: 켜짐, 자기 판넬 1개", j["enabled"] and j["panels"] == 1 and j["available"], str(j))

        seen = []
        st.ai = aimod.Assistant(st, FakeClient(lambda n, kw: (seen.append(n), resp(
            [tool(1, "list_panels")], "tool_use") if n == 1 else resp([text("A 정상")], "end_turn"))[1]))
        s, j = m("/api/ai/ask", {"question": "우리 판넬 어때?", "history": "잘못된 형식"})
        check("고객 질문 → 답", s == 200 and j["answer"] == "A 정상" and j["remaining"] == aimod.MONTHLY_LIMIT - 2, str(j)[:200])
        ev = st.list_events(limit=5)
        check("감사 기록(질문 앞부분·계정)", any(e["etype"] == "ai_ask" and "kim.a" in e["detail"] for e in ev))

        aimod.MONTHLY_LIMIT = 2
        s, j = m("/api/ai/ask", {"question": "또?"})
        check("월 한도 넘으면 429", s == 429 and "한도" in j["error"], str(j))
        s, j = m("/api/ai/status")
        check("상태에 사용량·한도", j["usage"]["questions"] == 2 and j["usage"]["remaining"] == 0, str(j["usage"]))
        s, j = adm("/api/ai/status")
        check("관리자 사용량은 JCC 몫(고객과 따로 셈)", j["usage"]["questions"] >= 6, str(j["usage"]))
        aimod.MONTHLY_LIMIT = 200
        s, j = adm("/api/ai/ask", {"question": "x" * 2000})
        check("긴 질문 400", s == 400)
        st.ai = aimod.Assistant(st, None, "서버에 ANTHROPIC_API_KEY가 설정되지 않았습니다")
        s, j = adm("/api/ai/status")
        check("키 없으면 available false + 이유", j["available"] is False and "ANTHROPIC_API_KEY" in j["reason"])
        s, j = adm("/api/ai/ask", {"question": "q"})
        check("키 없으면 질문 503", s == 503)

        print("\n=== AI 월간 해설 ===")
        from jcc_server import monthly
        check("숫자 검사: 원문 숫자만 통과", aimod.numbers_grounded("9월 경보 3건, 가동률 99.5%", '{"p":"2026-09","n":3,"u":99.50}')
              and not aimod.numbers_grounded("경보 37건", '{"n":3}'))
        good = FakeClient(lambda n, kw: resp([text("이번 달은 큰 문제 없이 지나갔습니다. 지금 상태를 유지하세요.")], "end_turn"))
        st.ai = aimod.Assistant(st, good)
        rep = monthly.issue(st, ca, "2026-09", "시험")
        check("고객사 AI 켜짐 → 해설 붙음", rep["ai_note"] and "유지" in rep["ai_note"]["text"]
              and good.calls[0]["system"][0]["text"] == aimod.MONTHLY_SYSTEM and "tools" not in good.calls[0])
        check("해설 입력은 리포트 JSON", "리포트 JSON" in good.calls[0]["messages"][0]["content"]
              and '"period":"2026-09"' in good.calls[0]["messages"][0]["content"])
        saved = st.list_monthly(ca)[0]["report"]
        check("스냅숏에 저장", saved.get("ai_note", {}).get("text") == rep["ai_note"]["text"])
        st.ai = aimod.Assistant(st, FakeClient(lambda n, kw: resp([text("경보가 4817건 났습니다.")], "end_turn")))
        check("리포트에 없는 숫자 → 해설 버림", monthly.issue(st, ca, "2026-09", "시험")["ai_note"] is None)

        def boom(n, kw):
            raise RuntimeError("network")
        st.ai = aimod.Assistant(st, FakeClient(boom))
        r = monthly.issue(st, ca, "2026-09", "시험")
        check("해설 실패해도 리포트는 발행", r is not None and r["ai_note"] is None)
        quiet = FakeClient(lambda n, kw: resp([text("x")], "end_turn"))
        st.ai = aimod.Assistant(st, quiet)
        st.accounts.set_ai(ca, False)
        check("고객사 AI 꺼짐 → 호출 없음", monthly.issue(st, ca, "2026-09", "시험")["ai_note"] is None and not quiet.calls)
    finally:
        srv.shutdown()
        st.close()
        os.unlink(db)

    print()
    if _fails:
        print(f"❌ {len(_fails)} FAIL: {_fails}")
        return 1
    print("✅ 전체 통과")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
