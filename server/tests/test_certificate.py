"""안전 관리 확인서 — 분기·연간 경계, 끝난 기간만, 위험 경보 목록(자동 조치 초·확인 분), 점검 포함, 개정 번호, 권한.

python -m tests.test_certificate   (server/ 에서)
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

os.environ["JCC_DASHBOARD_PW"] = "sc-admin-pw"
os.environ["JCC_DASHBOARD_USER"] = "jccops"
os.environ.pop("JCC_API_KEY", None)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from jcc_server import certificate as C
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
    s, e = C.bounds("2026-Q3")
    check("3분기 = 한국 시간 7/1 0시 ~ 10/1 0시", time.strftime("%Y-%m-%d %H", time.gmtime(s + 9 * H)) == "2026-07-01 00"
          and e - s == 92 * D)
    q4 = C.bounds("2026-Q4")
    check("4분기 → 다음 해 1/1", time.strftime("%Y-%m-%d", time.gmtime(q4[1] + 9 * H)) == "2027-01-01")
    y = C.bounds("2026")
    check("연간 = 365일", y[1] - y[0] == 365 * D)
    check("기간 형식", C.valid_period("2026-Q1") and C.valid_period("2026") and not C.valid_period("2026-Q5")
          and not C.valid_period("2026-09") and not C.valid_period("x"))
    check("이름", C.label("2026-Q3") == "2026년 3분기" and C.label("2026") == "2026년 연간")

    fd, db = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    st = Storage(db)
    ac = st.accounts
    ca, cb = ac.create_customer("A 제조"), ac.create_customer("B 물류")
    ac.assign_panel("panel-A", ca)
    ac.assign_panel("panel-B", cb)
    for dev, panel in (("ccm-a1", "panel-A"), ("ccm-b1", "panel-B")):
        st.ingest({"device_id": dev, "panel": panel, "panel_name": panel + " 판넬", "readings": [
            {"key": "h2_lel", "name": "수소 농도", "unit": "%LEL", "value": 0.5, "ok": True, "ts": s + 3 * D}]})
    with st._lock:
        c = st._conn
        c.execute("UPDATE devices SET first_seen=?", (s + 20 * D,))          # 분기 중간(7/21)에 설치
        c.executemany("INSERT INTO events (ts, device_id, sensor_key, etype, detail, source) VALUES (?, ?, ?, ?, ?, 'system')",
                      [(s + 30 * D + 42, "ccm-a1", "", "vent_open", "화재 징조 → 벤트 자동 개방")])
        c.executemany("INSERT INTO alarms (device_id, sensor_key, kind, severity, raised_at, cleared_at, acked_at, detail) "
                      "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                      [("ccm-a1", "h2_lel", "fire", "crit", s + 30 * D, s + 30 * D + H, s + 30 * D + 9 * 60, "수소 11 %LEL"),
                       ("ccm-a1", "", "silent", "crit", s + 40 * D, s + 40 * D + H, None, "침묵"),       # 끊김은 위험 목록 아님
                       ("ccm-a1", "h2_lel", "alarm_warn", "warn", s + 41 * D, s + 41 * D + H, None, "주의"),  # 주의는 목록 아님
                       ("ccm-b1", "h2_lel", "fire", "crit", s + 31 * D, s + 31 * D + H, None, "B 것"),
                       ("ccm-a1", "h2_lel", "fire", "crit", e + 2 * H, None, None, "기간 밖")])
        c.commit()

    print("\n=== 확인서 ===")
    try:
        C.build(st, ca, "2026-Q3", now=e - H)
        check("기간이 끝나기 전엔 거부", False)
    except ValueError:
        check("기간이 끝나기 전엔 거부", True)
    rep = C.build(st, ca, "2026-Q3", now=e + 3 * H)
    check("고객사·이름·종류", rep["customer"] == "A 제조" and rep["label"] == "2026년 3분기" and rep["kind"] == "certificate")
    check("감시 시작 = 설치일(분기 중간)", abs(rep["watch_from"] - (s + 20 * D)) < 1, str(rep["watch_from"] - s))
    cr = rep["crit"]
    check("위험 경보: A의 기간 안 1건만(끊김·주의·B·기간 밖 제외)", len(cr) == 1, str(cr))
    check("판넬이 스스로 조치까지 42초 · 확인까지 9분", cr and cr[0]["self_sec"] == 42 and cr[0]["ack_min"] == 9.0, str(cr))
    check("센서 이름", cr and cr[0]["sensor_name"] == "수소 농도", str(cr))
    check("면책 문구", "대신하지 않습니다" in rep["note"])
    check("월간 리포트와 같은 집계(판넬 1)", rep["summary"]["panels"] == 1)

    one = C.issue(st, ca, "2026-Q3", "jccops", now=e + 3 * H)
    check("발행 1차 번호", one and one["rev"] == 1 and one["no"] == f"JCC-SC-2026-Q3-{ca:03d}", str(one and one["no"]))
    two = C.issue(st, ca, "2026-Q3", "jccops", now=e + 4 * H)
    check("다시 발행하면 개정 2", two["rev"] == 2 and two["no"].endswith("-R2"))
    with st._lock:
        st._conn.execute("DELETE FROM alarms")
        st._conn.commit()
    check("기록이 지워져도 발행된 확인서는 그대로", len(st.get_certificate(ca, "2026-Q3")["report"]["crit"]) == 1)

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
        a("/api/login", {"user": "jccops", "password": "sc-admin-pw"})
        s1, j = a("/api/certificates/issue", {"customer_id": cb, "period": "2026-Q3"})
        check("JCC: B 발행", s1 == 200 and j["certificate"]["report"]["customer"] == "B 물류", str(j)[:200])
        s1, j = a("/api/certificates")
        check("JCC: 전체 목록", s1 == 200 and {r["customer_id"] for r in j["certificates"]} == {ca, cb})
        check("JCC: 끝나지 않은 기간 400", a("/api/certificates/issue", {"customer_id": ca, "period": "2099-Q1"})[0] == 400)
        check("JCC: 잘못된 기간 400", a("/api/certificates/issue", {"customer_id": ca, "period": "2026-09"})[0] == 400)
        check("JCC: 없는 고객사 400", a("/api/certificates/issue", {"customer_id": 999, "period": "2026-Q3"})[0] == 400)
        _, temp = ac.create_user("lee.b", "viewer", cb)
        v = client()
        v("/api/login", {"user": "lee.b", "password": temp})
        v("/api/me/password", {"old": temp, "new": "lee-b-pw-2026"})
        v("/api/login", {"user": "lee.b", "password": "lee-b-pw-2026"})
        s1, j = v(f"/api/certificates?customer_id={ca}")
        check("B 계정은 A를 콕 집어도 자기 것만", s1 == 200 and {r["customer_id"] for r in j["certificates"]} == {cb})
        check("고객은 발행 403", v("/api/certificates/issue", {"customer_id": cb, "period": "2026-Q3"})[0] == 403)
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
