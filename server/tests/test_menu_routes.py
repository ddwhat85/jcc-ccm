"""상단 메뉴 기능이 실제 서버에서 열리는지 — 화면이 부르는 읽기 주소가 404·500 없이 답하는지.

데모(demo-api.js)에서만 되고 운영 서버엔 처리기가 없는 '껍데기 기능'을 막는다.
python -m tests.test_menu_routes   (server/ 에서)
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
from jcc_server.storage import Storage

_fails = []

# 메뉴·화면이 여는 읽기 주소(쿼리 포함). 쓰기(POST)는 각 기능 테스트가 맡는다.
GETS = [
    "/api/fleet", "/api/panels", "/api/devices", "/api/incidents", "/api/alarms/history?days=30",
    "/api/report?days=7", "/api/monthly", "/api/commission/reports", "/api/inspections",
    "/api/inspection?panel=p1", "/api/predict", "/api/rul", "/api/events", "/api/ai/status",
    "/api/admin/accounts", "/api/tuning/config", "/api/tuning/params", "/api/tuning/scenarios",
    "/api/sensor/profiles", "/api/sensor/manual?device_id=ccm-1", "/api/heal/config",
    "/api/devices/ccm-1/history?sensor=t",
    "/api/export/alarms.csv?days=7", "/api/export/readings.csv?days=1&device_id=ccm-1&sensor=t",
]


def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not cond:
        _fails.append(name)


def run():
    fd, db = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    st = Storage(db)
    st.ingest({"device_id": "ccm-1", "panel": "p1", "panel_name": "1번", "readings": [
        {"key": "t", "name": "온도", "unit": "C", "value": 25, "ok": True, "ts": time.time()}]})
    srv = appmod.make_server("127.0.0.1", 0, st)
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def call(path, body=None):
        req = urllib.request.Request(base + path, method="POST" if body is not None else "GET",
                                     data=None if body is None else json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        try:
            with op.open(req, timeout=15) as r:
                return r.status
        except urllib.error.HTTPError as e:
            return e.code
        except Exception as e:  # noqa: BLE001 - 응답 없이 끊김도 실패로
            return f"끊김({type(e).__name__})"

    try:
        call("/api/login", {"user": "jccops", "password": "env-admin-pw"})
        for p in GETS:
            s = call(p)
            check(f"GET {p}", s == 200, str(s))
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
