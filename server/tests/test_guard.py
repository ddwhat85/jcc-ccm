"""고객 화면(JCC GUARD) — 안심 지수 감점·상태 문장·판넬 모델·위험 순간·이번 달 장부·고객 범위·경로.

python -m tests.test_guard   (server/ 에서)
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

os.environ["JCC_DASHBOARD_PW"] = "env-admin-pw"
os.environ["JCC_DASHBOARD_USER"] = "jccops"
os.environ.pop("JCC_API_KEY", None)
os.environ["JCC_SUPPORT_PHONE"] = "02-0000-0000"
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from jcc_server import app as appmod
from jcc_server import guard
from jcc_server.storage import Storage

_fails = []


def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not cond:
        _fails.append(name)


def row(name="1번", crit=0, warn=0, on=1, tot=1, fire=None, contact=None, dew=None, life=None, due=None, status="ok", why=None):
    return {"panel": name, "panel_name": name, "status": status, "why": why or [],
            "ccm_online": on, "ccm_total": tot, "alarms": {"crit": crit, "warn": warn, "unacked": crit + warn},
            "predict": {"fire": fire, "contact": contact, "dew": dew, "vent_open": False},
            "life": life, "next_inspection": due}


def unit(now):
    s = guard.score([row()], now)
    check("이상 없으면 100·초록", s["score"] == 100 and s["color"] == "ok" and s["items"] == [])
    s = guard.score([row(warn=1, dew="warning"), row("2번", life={"label": "필터", "say": "약 7주", "days": 49, "status": "ok"})], now)
    check("주의 −5·결로 −3·여유 −3 = 89·호박", s["score"] == 89 and s["color"] == "warn", str(s))
    check("감점 이유에 판넬 이름", any("1번" in i["text"] and i["minus"] == 5 for i in s["items"]))
    s = guard.score([row(crit=1, on=0, tot=2, fire="watch", due=now - 86400)], now)
    check("위험 −20·끊김 2대 −20·화재 지켜봄 −5·점검 기한 −5 = 50·빨강", s["score"] == 50 and s["color"] == "crit", str(s))
    s = guard.score([row(crit=6)], now)
    check("0 아래로 안 내려감", s["score"] == 0)
    check("고정 점검 항목(위험 경보·끊긴 장치·점검 기한)", [c["label"] for c in guard.score([row()], now)["checks"]][:2] == ["위험 경보", "끊긴 감시 장치"])
    check("상태 문장: 안전", guard.state_line([row(), row("2번")])["title"] == "모든 판넬 안전합니다")
    st = guard.state_line([row(status="warn", why=["결로 위험"]), row("2번")])
    check("상태 문장: 지켜보는 중", st["title"] == "판넬 2면 · 1곳 지켜보는 중" and "결로 위험" in st["sub"], str(st))
    st = guard.state_line([row("A동", crit=1, status="crit", why=["화재 징조(FRI 72)"])])
    check("상태 문장: 위험", st["title"].startswith("위험") and "A동" in st["title"], str(st))
    check("상태 문장: 판넬 없음", "아직" in guard.state_line([])["title"])


def run():
    now = time.time()
    unit(now)
    fd, db = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    st = Storage(db)
    for dev, panel, nm in (("ccm-1", "p1", "A동 배터리실"), ("ccm-2", "p2", "B동 수배전")):
        st.ingest({"device_id": dev, "panel": panel, "panel_name": nm, "readings": [
            {"key": "cabinet_temp", "name": "함내 온도", "unit": "C", "kind": "temp", "value": 31.2, "ok": True, "ts": now},
            {"key": "cabinet_humidity", "name": "함내 습도", "unit": "%RH", "kind": "humidity", "value": 78, "ok": True, "ts": now}]})
    cid = st.accounts.create_customer("평택 2공장")
    st.accounts.assign_panel("p1", cid)

    v = guard.guard_view(st, None, "전체")
    names = [p["panel_name"] for p in v["panels"]]
    check("판넬 모델: 온도·습도 값", v["panels"][0]["temp"] == 31.2 and v["panels"][0]["humidity"] == 78, str(v["panels"][0]))
    check("판넬 2면·지수 100", sorted(names) == ["A동 배터리실", "B동 수배전"] and v["index"]["score"] == 100, str(v["index"]))
    check("위험 없으면 incident 없음", v["incident"] is None)
    check("연락처: 환경변수 전화", v["contact"]["phone"] == "02-0000-0000")
    vc = guard.guard_view(st, {"p1"}, "평택 2공장")
    check("고객 범위: 자기 판넬만", [p["panel_name"] for p in vc["panels"]] == ["A동 배터리실"] and vc["site"] == "평택 2공장")

    # 위험 순간: 위험 경보 → 판넬이 스스로 환기(이벤트) → JCC 확인
    st.raise_alarm("ccm-1", "cabinet_temp", "alarm", "함내 온도 61C — 위험(55 초과)")
    st.log_event("ccm-1", "", "vent_open", "화재 징조 → 벤트 자동 개방", source="system")
    aid = next(a["id"] for a in st.list_active_alarms())
    st.ack_alarm(aid, "김현장")
    vi = guard.guard_view(st, {"p1"}, "평택 2공장")
    inc = vi["incident"]
    check("위험 순간: 판넬·센서·내용", inc and inc["panel_name"] == "A동 배터리실" and inc["sensor_name"] == "함내 온도", str(inc))
    texts = [s["text"] for s in inc["steps"]]
    check("단계: 판넬이 스스로 → JCC 확인", any("스스로" in t for t in texts) and any("김현장" in t for t in texts), str(texts))
    check("위험이면 지수 빨강", vi["index"]["color"] == "crit" and vi["index"]["score"] <= 80)
    check("다른 고객 범위엔 위험 안 보임", guard.guard_view(st, {"p2"}, "x")["incident"] is None)

    m = guard.month_view(st, {"p1"}, time.strftime("%Y-%m", time.localtime(now)))
    check("이번 달: 키 모양", {"hours", "patrols", "precursors", "actions", "remote", "ack_min_avg", "building"} <= set(m), str(m))
    check("이번 달: 스스로 한 조치에 환기 1", m["actions"]["total"] >= 1 and m["actions"]["vent"] >= 1, str(m["actions"]))
    check("이번 달: 순찰 추정은 하루 3회 기준", m["patrols"]["per_day"] == 3)
    d = guard.panel_detail(st, "p1")
    check("판넬 상세: 타임라인에 환기", any("벤트" in t["text"] for t in d["timeline"]), str(d["timeline"][:3]))
    check("없는 판넬은 None", guard.panel_detail(st, "zz") is None)

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
                    raw = r.read()
                    return r.status, (json.loads(raw) if r.headers.get_content_type() == "application/json" else raw)
            except urllib.error.HTTPError as e:
                return e.code, {}
        return call

    try:
        anon = client()
        s, body = anon("/guard")
        check("/guard 화면은 공개", s == 200 and b"JCC GUARD" in body, str(s))
        check("/api/guard 로그인 전 401", anon("/api/guard")[0] == 401)
        adm = client()
        adm("/api/login", {"user": "jccops", "password": "env-admin-pw"})
        s, j = adm("/api/guard")
        check("직원: 전체 미리보기", s == 200 and len(j["panels"]) == 2, str(s))
        s, j = adm(f"/api/guard?customer_id={cid}")
        check("직원: 고객사 미리보기", s == 200 and [p["panel"] for p in j["panels"]] == ["p1"] and j["site"] == "평택 2공장")
        _, temp = st.accounts.create_user("view.g", "viewer", cid)
        cu = client()
        cu("/api/login", {"user": "view.g", "password": temp})
        cu("/api/me/password", {"old": temp, "new": "view-g-pw-2026"})
        cu("/api/login", {"user": "view.g", "password": "view-g-pw-2026"})
        s, j = cu("/api/guard?customer_id=999")
        check("고객: customer_id 무시하고 자기 범위", s == 200 and [p["panel"] for p in j["panels"]] == ["p1"])
        check("고객: 남의 판넬 상세 404", cu("/api/guard/panel?panel=p2")[0] == 404)
        check("고객: 자기 판넬 상세 200", cu("/api/guard/panel?panel=p1")[0] == 200)
        check("이번 달 경로", cu("/api/guard/month?period=" + time.strftime("%Y-%m"))[0] == 200)
        check("잘못된 달 400", cu("/api/guard/month?period=abc")[0] == 400)
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
