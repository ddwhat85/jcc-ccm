"""고객용 월간 리포트 — 기간 경계·지표·다른 고객사 제외·권고·자동 발행 1회·알림은 켠 곳만·권한.

python -m tests.test_monthly   (server/ 에서)
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

os.environ["JCC_DASHBOARD_PW"] = "mr-admin-pw"
os.environ["JCC_DASHBOARD_USER"] = "jccops"
os.environ.pop("JCC_API_KEY", None)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from jcc_server import monthly as M
from jcc_server.app import make_server
from jcc_server.storage import Storage

_fails = []


def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not cond:
        _fails.append(name)


H, D = 3600.0, 86400.0


def run():
    print("=== 기간 ===")
    s, e = M.month_bounds("2026-09")
    check("9월 = 한국 시간 9/1 0시 ~ 10/1 0시", time.strftime("%Y-%m-%d %H", time.gmtime(s + 9 * H)) == "2026-09-01 00"
          and e - s == 30 * D)
    check("12월 → 다음 해 1월", M.month_bounds("2026-12")[1] - M.month_bounds("2026-12")[0] == 31 * D)
    check("10/1 01시(한국)의 지난달 = 2026-09", M.prev_period(e + 1 * H) == "2026-09")
    check("9/30 23시(한국)의 지난달 = 2026-08", M.prev_period(e - 1 * H) == "2026-08")
    check("기간 형식 검사", M.valid_period("2026-09") and not M.valid_period("2026-13") and not M.valid_period("x"))

    fd, db = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    st = Storage(db)
    ac = st.accounts
    ca, cb = ac.create_customer("A 제조"), ac.create_customer("B 물류")
    ac.assign_panel("panel-A", ca)
    ac.assign_panel("panel-B", cb)
    for dev, panel in (("ccm-a1", "panel-A"), ("ccm-b1", "panel-B")):
        st.ingest({"device_id": dev, "panel": panel, "panel_name": panel + " 판넬", "readings": [
            {"key": "h2_lel", "name": "수소", "unit": "%LEL", "value": 0.5, "ok": True, "ts": s + 3 * D}]})
    with st._lock:
        c = st._conn
        c.execute("UPDATE devices SET first_seen=?", (s - 10 * D,))
        ev = [("ccm-a1", "h2_lel", "fire", s + 1 * D), ("ccm-a1", "", "vent_open", s + 1 * D + 60),
              ("ccm-a1", "h2_lel", "drift", s + 4 * D), ("ccm-b1", "h2_lel", "fire", s + 2 * D),
              ("ccm-a1", "h2_lel", "fire", e + 1 * H)]                       # 기간 밖
        c.executemany("INSERT INTO events (ts, device_id, sensor_key, etype, detail, source) VALUES (?, ?, ?, ?, ?, 'system')",
                      [(t, d, k, et, et) for d, k, et, t in ev])
        al = [("ccm-a1", "", "silent", "crit", s + 2 * D, s + 2 * D + 3 * H, None),
              ("ccm-a1", "h2_lel", "fire", "crit", s + 1 * D, s + 1 * D + 2 * H, s + 1 * D + 12 * 60),
              ("ccm-a1", "vent", "actuator_fault", "crit", s + 5 * D, s + 5 * D + H, None),
              ("ccm-b1", "h2_lel", "alarm", "crit", s + 6 * D, s + 6 * D + H, None)]
        c.executemany("INSERT INTO alarms (device_id, sensor_key, kind, severity, raised_at, cleared_at, acked_at, detail) "
                      "VALUES (?, ?, ?, ?, ?, ?, ?, 'x')", al)
        c.commit()

    print("\n=== A 제조 9월 ===")
    rep = M.build_monthly(st, ca, "2026-09", now=e + 2 * H)
    sm = rep["summary"]
    check("판넬 1·고객사명", rep["customer"] == "A 제조" and sm["panels"] == 1 and rep["panels"][0]["panel_name"] == "panel-A 판넬")
    check("징조: A의 9월 화재 1건만(B·기간 밖 제외)", sm["fire"]["detected"] == 1, str(sm["fire"]))
    check("벤트 자동 1", sm["vent_auto"] == 1)
    check("위험 경보 3(A만)·확인까지 12분", sm["alarms_crit"] == 3 and sm["ack_min_avg"] == 12.0, str(sm))
    check("가동률 = 침묵 3시간 뺀 99.58%", sm["uptime"] == round(100 * (1 - 3 * H / (30 * D)), 2), str(sm["uptime"]))
    check("판넬 표: 작동 실패 1", rep["panels"][0]["faults"] == 1 and sm["faults"] == 1)
    adv = " ".join(rep["advice"])
    check("권고: 드리프트 교정·작동 실패 점검", "교정" in adv and "구동기" in adv, str(rep["advice"]))
    rb = M.build_monthly(st, cb, "2026-09", now=e + 2 * H)
    check("B 제조엔 A 수치가 섞이지 않음", rb["summary"]["fire"]["detected"] == 1 and rb["summary"]["alarms_crit"] == 1
          and rb["summary"]["faults"] == 0)
    check("없는 고객사 → None", M.build_monthly(st, 999, "2026-09") is None)

    print("\n=== 자동 발행·알림 ===")
    sent = []
    ac.set_receivers(ca, ["01011112222"])
    ac.set_monthly_notify(ca, True)
    n = M.generate_due(st, now=e + 1 * H, send=lambda text, nums: sent.append((text, nums)))
    check("10/1에 고객사마다 9월 발행", n == 2 and st.has_monthly(ca, "2026-09") and st.has_monthly(cb, "2026-09"))
    check("알림은 켠 A에만 한 통", len(sent) == 1 and sent[0][1] == ["01011112222"] and "9월" in sent[0][0], str(sent))
    check("알림 문구는 고객 화면 길로(직원 메뉴 경로 아님)", "고객 화면 → 지켜낸 것" in sent[0][0] and "대시보드" not in sent[0][0], sent[0][0])
    n = M.generate_due(st, now=e + 2 * H, send=lambda text, nums: sent.append((text, nums)))
    check("다시 돌아도 중복 발행·중복 알림 없음", n == 0 and len(sent) == 1)
    saved = st.list_monthly(ca)
    check("발행 스냅숏 저장", saved and saved[0]["report"]["summary"]["fire"]["detected"] == 1)
    with st._lock:
        st._conn.execute("DELETE FROM events")
        st._conn.commit()
    check("데이터가 지워져도 발행된 문서는 그대로", st.list_monthly(ca)[0]["report"]["summary"]["fire"]["detected"] == 1)

    print("\n=== API 권한 ===")
    srv = make_server("127.0.0.1", 0, st)
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
            except urllib.error.HTTPError as ex:
                return ex.code, json.loads(ex.read().decode("utf-8") or "{}")
        return call
    try:
        a = client()
        a("/api/login", {"user": "jccops", "password": "mr-admin-pw"})
        s1, j = a("/api/monthly")
        check("JCC: 전체 목록", s1 == 200 and {r["customer_id"] for r in j["reports"]} == {ca, cb})
        s1, j = a(f"/api/monthly?customer_id={cb}")
        check("JCC: 고객사 필터", {r["customer_id"] for r in j["reports"]} == {cb})
        s1, j = a("/api/monthly/issue", {"customer_id": ca, "period": "2026-10"})
        check("JCC: 지금 발행(이번 달 중간)", s1 == 200 and j["report"]["period"] == "2026-10")
        check("JCC: 잘못된 월 거부", a("/api/monthly/issue", {"customer_id": ca, "period": "2026-13"})[0] == 400)
        s1, _ = a("/api/admin/monthly_notify", {"customer_id": cb, "on": True})
        check("JCC: 알림 스위치", s1 == 200 and next(x for x in ac.list_customers() if x["id"] == cb)["monthly_notify"])
        _, temp = ac.create_user("lee.b", "viewer", cb)
        v = client()
        v("/api/login", {"user": "lee.b", "password": temp})
        v("/api/me/password", {"old": temp, "new": "lee-b-pw-2026"})
        v("/api/login", {"user": "lee.b", "password": "lee-b-pw-2026"})
        s1, j = v(f"/api/monthly?customer_id={ca}")
        check("B 계정은 A를 콕 집어도 자기 것만", s1 == 200 and {r["customer_id"] for r in j["reports"]} == {cb})
        check("고객은 발행 403", v("/api/monthly/issue", {"customer_id": cb, "period": "2026-09"})[0] == 403)
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
