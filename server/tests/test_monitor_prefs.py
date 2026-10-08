"""모니터링 모드 보기 설정 — 계정마다 저장, 다시 켜도 그대로, 남의 판넬은 저장 안 함.

python -m tests.test_monitor_prefs   (server/ 에서)
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
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from jcc_server import app as appmod
from jcc_server.storage import Storage

_fails = []


def check(name, cond, detail=""):
    print(("[PASS] " if cond else "[FAIL] ") + name + (f" — {detail}" if detail else ""))
    if not cond:
        _fails.append(name)


def run() -> int:
    fd, db = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    st = Storage(db)
    now = time.time()
    for dev, pn in (("ccm-1", "p1"), ("ccm-2", "p2"), ("ccm-3", "p3")):
        st.ingest({"device_id": dev, "panel": pn, "panel_name": pn, "readings": [
            {"key": "cabinet_temp", "name": "함내 온도", "unit": "C", "kind": "temp", "value": 30, "ok": True, "ts": now}]})
    cid = st.accounts.create_customer("평택")
    st.accounts.assign_panel("p1", cid)
    st.accounts.assign_panel("p2", cid)
    _, temp = st.accounts.create_user("wall.m", "viewer", cid)
    srv = appmod.make_server("127.0.0.1", 0, st)
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def call(path, body=None):
        req = urllib.request.Request(base + path, method="POST" if body is not None else "GET",
                                     data=None if body is None else json.dumps(body).encode(), headers={"Content-Type": "application/json"})
        try:
            with op.open(req, timeout=10) as res:
                return res.status, json.loads(res.read() or b"{}")
        except urllib.error.HTTPError as e:
            return e.code, {}
    try:
        call("/api/login", {"user": "wall.m", "password": temp})
        call("/api/me/password", {"old": temp, "new": "wall-m-pw-2026"})
        call("/api/login", {"user": "wall.m", "password": "wall-m-pw-2026"})
        c, j = call("/api/guard/monitor_prefs")
        check("처음엔 빈 설정", c == 200 and j["prefs"] == {}, str(j))
        c, j = call("/api/guard/monitor_prefs", {"hidden": ["p2", "p3"], "order": ["p2", "p1", "p3"], "pin": {"p1": "ccm-1:cabinet_temp", "p3": "x:y"},
                                                 "problems_first": False})
        p = j.get("prefs") or {}
        check("저장: 남의 판넬(p3)은 빠짐", c == 200 and p["hidden"] == ["p2"] and p["order"] == ["p2", "p1"] and p["pin"] == {"p1": "ccm-1:cabinet_temp"}
              and p["problems_first"] is False, str(p))
        c, j = call("/api/guard/monitor_prefs")
        check("다시 불러도 그대로", j["prefs"] == p)
        check("객체가 아니면 400", call("/api/guard/monitor_prefs", "x")[0] == 400)
    finally:
        srv.shutdown()
        st.close()
        os.unlink(db)
    print("\n" + ("✅ 전체 통과" if not _fails else f"❌ 실패 {len(_fails)}건: {_fails}"))
    return 0 if not _fails else 1


if __name__ == "__main__":
    sys.exit(run())
