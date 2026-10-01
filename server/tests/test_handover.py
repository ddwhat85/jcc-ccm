"""근무 인계 요약 — 기준 시각(처음 12시간·최대 7일·확인하면 지금부터)·할 일·있었던 일·고객 범위.

python -m tests.test_handover   (server/ 에서)
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

from jcc_server import app as appmod
from jcc_server import handover
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
    for dev, panel in (("ccm-1", "p1"), ("ccm-2", "p2")):
        st.ingest({"device_id": dev, "panel": panel, "panel_name": panel.upper(), "readings": [
            {"key": "t", "name": "온도", "unit": "C", "value": 25, "ok": True, "ts": now}]})
    with st._lock:   # 20시간 전 경보(처음 기준 12시간 밖) · 1시간 전 해제된 경보(원인 없음)
        st._conn.execute("INSERT INTO alarms (device_id, sensor_key, kind, detail, severity, raised_at, cleared_at) "
                         "VALUES ('ccm-1','t','alarm','옛 경보','crit',?,?)", (now - 20 * 3600, now - 19 * 3600))
        st._conn.execute("INSERT INTO alarms (device_id, sensor_key, kind, detail, severity, raised_at, cleared_at) "
                         "VALUES ('ccm-1','t','alarm_warn','지난 경보','warn',?,?)", (now - 3600, now - 3000))
        st._conn.commit()
    st.raise_alarm("ccm-1", "t", "alarm", "온도 위험")
    st.raise_alarm("ccm-2", "t", "alarm", "2번 온도 위험")
    old = next(a["id"] for a in st.alarms_since(0, None, 10) if a["detail"] == "지난 경보")
    st.ack_alarm(old, "kim", "팬 필터 청소", "real")

    s = handover.summary(st, "lee")
    check("처음엔 12시간 전부터", abs(s["since"] - (now - 12 * 3600)) < 5 and s["seen"] is None)
    check("있었던 일: 12시간 안 경보만", s["happened"]["alarms"] == 3 and s["happened"]["crit"] == 2, str(s["happened"]))
    check("할 일: 미확인 2건, 이름 붙음", s["todo"]["unacked"] == 2 and s["todo"]["unacked_list"][0]["panel_name"] in ("P1", "P2")
          and s["todo"]["unacked_list"][0]["sensor_name"] == "온도")
    check("원인 메모가 인계에", any("팬 필터 청소" in n["detail"] for n in s["happened"]["notes"]), str(s["happened"]["notes"]))
    check("조용하지 않음", s["quiet"] is False)
    handover.mark_seen(st, "lee", now + 1)
    s2 = handover.summary(st, "lee", now=now + 2)
    check("확인하면 그 뒤부터 — 새 경보 0, 미확인은 여전히 할 일", s2["happened"]["alarms"] == 0 and s2["todo"]["unacked"] == 2)
    with st._lock:
        st._conn.execute("UPDATE handover_seen SET ts=? WHERE username='lee'", (now - 30 * 86400,))
        st._conn.commit()
    check("오래 안 봤어도 최대 7일", abs(handover.summary(st, "lee", now=now)["since"] - (now - 7 * 86400)) < 5)
    check("사람마다 따로", handover.last_seen(st, "park") is None)
    sc = handover.summary(st, "kim.a", {"ccm-1", "p1"}, {"p1"})
    check("고객 범위: 자기 판넬만", sc["todo"]["unacked"] == 1 and all(a["panel_name"] == "P1" for a in sc["todo"]["unacked_list"]))

    srv = appmod.make_server("127.0.0.1", 0, st)
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    threading.Thread(target=srv.serve_forever, daemon=True).start()
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
    try:
        check("로그인 전 401", call("/api/handover")[0] == 401)
        call("/api/login", {"user": "jccops", "password": "env-admin-pw"})
        st_, j = call("/api/handover")
        check("GET 인계", st_ == 200 and j["todo"]["unacked"] == 2)
        call("/api/handover/seen", {})
        check("확인 후 기준이 지금", call("/api/handover")[1]["seen"] is not None
              and handover.last_seen(st, "jccops") > now)
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
