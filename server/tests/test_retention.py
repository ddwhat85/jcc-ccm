"""데이터 보관 — 기본 정리, 장기 보관 고객사는 경보·활동·시간별 값을 그 햇수만큼, 원본은 그대로 14일, 내보내기 범위·권한.

python -m tests.test_retention   (server/ 에서)
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

os.environ["JCC_DASHBOARD_PW"] = "rt-admin-pw"
os.environ["JCC_DASHBOARD_USER"] = "jccops"
os.environ.pop("JCC_API_KEY", None)
for k in ("JCC_KEEP_READING_DAYS", "JCC_KEEP_EVENT_DAYS", "JCC_KEEP_HOURLY_DAYS"):
    os.environ.pop(k, None)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from jcc_server import forecast, retention
from jcc_server.app import make_server
from jcc_server.storage import Storage

_fails = []
D = 86400.0


def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not cond:
        _fails.append(name)


def run():
    check("기본 보관", retention.plan(0) == {"years": 0, "readings_days": 14, "events_days": 90, "hourly_days": 1100})
    check("5년 보관", retention.plan(5)["events_days"] == 5 * 365 + 5 and retention.plan(5)["hourly_days"] == 5 * 365 + 5
          and retention.plan(5)["readings_days"] == 14)
    check("모르는 햇수는 기본", retention.plan(7)["years"] == 0)

    fd, db = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    st = Storage(db)
    ac = st.accounts
    ca, cb = ac.create_customer("장기 고객"), ac.create_customer("기본 고객")
    for dev, panel, cid in (("ccm-long", "p-long", ca), ("ccm-base", "p-base", cb)):
        st.ingest({"device_id": dev, "panel": panel, "panel_name": panel, "readings": [
            {"key": "cabinet_temp", "name": "함내 온도", "unit": "C", "kind": "temp", "value": 30, "ok": True, "ts": time.time()}]})
        ac.assign_panel(panel, cid)
    ac.set_keep_years(ca, 5)
    try:
        ac.set_keep_years(ca, 4)
        check("보관 햇수는 기본·3·5만", False)
    except ValueError:
        check("보관 햇수는 기본·3·5만", True)
    now = time.time()
    forecast.ensure(st)
    with st._lock:
        c = st._conn
        for dev in ("ccm-long", "ccm-base"):
            for age in (30, 200, 1500, 2000):
                c.execute("INSERT INTO events (ts, device_id, sensor_key, etype, detail, source) VALUES (?, ?, '', 'vent_open', 'x', 'system')",
                          (now - age * D, dev))
                c.execute("INSERT INTO alarms (device_id, sensor_key, kind, severity, raised_at, cleared_at, detail) VALUES (?, 'k', 'alarm', 'crit', ?, ?, 'x')",
                          (dev, now - age * D, now - age * D + 60))
                c.execute("INSERT INTO hourly (device_id, sensor_key, hour, avg, vmin, vmax, n) VALUES (?, 'cabinet_temp', ?, 30, 29, 31, 60)",
                          (dev, now - age * D))
            c.execute("INSERT INTO readings (device_id, sensor_key, name, unit, value, ok, ts) VALUES (?, 'cabinet_temp', '함내 온도', 'C', 30, 1, ?)",
                      (dev, now - 30 * D))
        c.commit()
    long = retention.long_keep(st)
    check("장기 보관 키 = 그 고객사 기기·판넬", set(long) == {"ccm-long", "p-long"} and long["ccm-long"] == 5 * 365 + 5, str(long))
    st.prune(readings_days=14, events_days=90, long=long, now=now)
    forecast.prune(st, now, long)

    def ages(table, col, dev):
        with st._lock:
            return sorted(round((now - r[0]) / D) for r in st._conn.execute(f"SELECT {col} FROM {table} WHERE device_id=?", (dev,)).fetchall())
    check("기본 고객: 활동 기록 90일까지만", ages("events", "ts", "ccm-base") == [30], str(ages("events", "ts", "ccm-base")))
    check("장기 고객: 활동 기록 5년까지(2000일은 지움)", ages("events", "ts", "ccm-long") == [30, 200, 1500], str(ages("events", "ts", "ccm-long")))
    check("장기 고객: 해제된 경보도 5년까지", ages("alarms", "raised_at", "ccm-long") == [30, 200, 1500])
    check("기본 고객: 시간별 값 3년까지", ages("hourly", "hour", "ccm-base") == [30, 200], str(ages("hourly", "hour", "ccm-base")))
    check("장기 고객: 시간별 값 5년까지", ages("hourly", "hour", "ccm-long") == [30, 200, 1500], str(ages("hourly", "hour", "ccm-long")))
    check("원본 측정값은 누구든 14일", ages("readings", "ts", "ccm-long")[:1] == [0] and 30 not in ages("readings", "ts", "ccm-long"))

    print("\n=== 내보내기 ===")
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
                    raw = r.read()
                    return r.status, raw
            except urllib.error.HTTPError as ex:
                return ex.code, ex.read()
        return call

    def login(name, cid):
        _, temp = ac.create_user(name, "viewer", cid)
        cl = client()
        cl("/api/login", {"user": name, "password": temp})
        cl("/api/me/password", {"old": temp, "new": name + "-pw-2026x"})
        cl("/api/login", {"user": name, "password": name + "-pw-2026x"})
        return cl
    try:
        lg = login("long.a", ca)
        s1, raw = lg("/api/export/events.csv?days=1900")
        txt = raw.decode("utf-8-sig")
        check("장기 고객: 5년 범위로 활동 기록 받음(1500일 전 것 포함, 남의 것 없음)", s1 == 200 and txt.count("vent_open") == 3
              and "ccm-base" not in txt, txt[:200])
        s1, raw = lg("/api/export/hourly.csv?days=1900")
        txt = raw.decode("utf-8-sig")
        check("시간별 값 CSV(한글 머리줄·평균·최저·최고)", s1 == 200 and txt.startswith("시각(1시간)") and txt.count("ccm-long") == 3, txt[:200])
        bs = login("base.b", cb)
        s1, raw = bs("/api/export/alarms.csv?days=1900")
        check("기본 고객: 1년까지로 잘림·자기 것만", s1 == 200 and "ccm-long" not in raw.decode("utf-8-sig"))
        a = client()
        a("/api/login", {"user": "jccops", "password": "rt-admin-pw"})
        s1, raw = a("/api/admin/keep_years", {"customer_id": cb, "years": 3})
        check("JCC: 보관 기간 3년으로", s1 == 200 and next(c for c in ac.list_customers() if c["id"] == cb)["keep_years"] == 3)
        check("JCC: 잘못된 햇수 400", a("/api/admin/keep_years", {"customer_id": cb, "years": 4})[0] == 400)
        s1, raw = lg("/api/guard")
        check("고객 화면에 보관 기간", s1 == 200 and json.loads(raw)["keep"]["years"] == 5)
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
