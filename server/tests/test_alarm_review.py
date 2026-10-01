"""경보 이력 — 원인 거르기·해제된 경보에 나중 메모·오경보 잦은 센서.

python -m tests.test_alarm_review   (server/ 에서)
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
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from jcc_server import alarm_review
from jcc_server import app as appmod
from jcc_server.storage import Storage

_fails = []


def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not cond:
        _fails.append(name)


def run():
    fd, db = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    st = Storage(db)
    now = time.time()
    st.ingest({"device_id": "ccm-1", "panel": "p1", "panel_name": "1번", "readings": [
        {"key": "h2", "name": "수소", "unit": "%LEL", "value": 0.1, "ok": True, "ts": now}]})
    with st._lock:
        for i, c in enumerate(["false", "false", "work", "real", None]):
            st._conn.execute("INSERT INTO alarms (device_id, sensor_key, kind, detail, severity, raised_at, cleared_at, cause) "
                             "VALUES ('ccm-1','h2','alarm','x','crit',?,?,?)", (now - 3600 + i, now - 3000 + i, c))
        st._conn.commit()
    unlabeled = next(a for a in st.alarms_since(0, None, 100) if not a.get("cause"))
    ok = st.ack_alarm(unlabeled["id"], "kim", "센서 청소", "false")
    a = st.get_alarm(unlabeled["id"])
    check("확인 없이 해제된 경보에도 나중에 원인·메모", ok and a["cause"] == "false" and a["ack_note"] == "센서 청소"
          and a["acked_at"] is None)
    h = alarm_review.history(st, None, 7)
    check("이력: 이름·지속시간", h["total"] == 5 and h["alarms"][0]["sensor_name"] == "수소" and h["alarms"][0]["minutes"] == 10.0)
    check("원인으로 거르기", alarm_review.history(st, None, 7, "false")["total"] == 3
          and alarm_review.history(st, None, 7, "none")["total"] == 0)
    n = h["noisy"]
    check("오경보 잦은 센서 짚음", len(n) == 1 and n[0]["false"] == 4 and n[0]["sensor_name"] == "수소", str(n))
    check("고객 범위 밖이면 비어 있음", alarm_review.history(st, {"ccm-x"}, 7)["total"] == 0)

    # HTTP: 고객 범위·등급(보기 전용은 메모 못 씀, 담당자는 자기 판넬만)
    st.ingest({"device_id": "ccm-2", "panel": "p2", "panel_name": "2번", "readings": [
        {"key": "t", "name": "온도", "unit": "C", "value": 20, "ok": True, "ts": now}]})
    with st._lock:
        st._conn.execute("INSERT INTO alarms (device_id, sensor_key, kind, detail, severity, raised_at, cleared_at) "
                         "VALUES ('ccm-2','t','alarm','y','crit',?,?)", (now - 100, now - 50))
        st._conn.commit()
    other = next(a["id"] for a in st.alarms_since(0, {"ccm-2"}, 10))
    mine = next(a["id"] for a in st.alarms_since(0, {"ccm-1"}, 10))
    cid = st.accounts.create_customer("A")
    st.accounts.assign_panel("p1", cid)
    srv = appmod.make_server("127.0.0.1", 0, st)
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    def client(user, role):
        _, temp = st.accounts.create_user(user, role, cid)
        op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

        def call(path, body=None):
            req = urllib.request.Request(base + path, method="POST" if body is not None else "GET",
                                         data=None if body is None else json.dumps(body).encode(),
                                         headers={"Content-Type": "application/json"})
            try:
                with op.open(req, timeout=10) as r:
                    return r.status, json.loads(r.read() or b"{}")
            except urllib.error.HTTPError as e:
                return e.code, {}
        call("/api/login", {"user": user, "password": temp})
        call("/api/me/password", {"old": temp, "new": user + "-pw-20261"})
        call("/api/login", {"user": user, "password": user + "-pw-20261"})
        return call

    try:
        v = client("view.a", "viewer")
        s, j = v("/api/alarms/history?days=7")
        check("고객: 자기 판넬 경보만", s == 200 and {a["device_id"] for a in j["alarms"]} == {"ccm-1"}, str(s))
        check("잘못된 days·원인 값도 200", v("/api/alarms/history?days=abc&cause=zzz")[0] == 200)
        check("보기 전용은 메모 못 씀", v("/api/alarm/ack", {"alarm_id": mine, "note": "x"})[0] == 403)
        m = client("mgr.a", "manager")
        check("담당자: 자기 판넬 해제 경보에 메모", m("/api/alarm/ack", {"alarm_id": mine, "note": "단자 재조임"})[1].get("ok") is True)
        check("담당자: 남의 판넬 경보는 403", m("/api/alarm/ack", {"alarm_id": other, "note": "x"})[0] == 403)
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
