"""사람별 권한 — 기본값은 등급 그대로, JCC 관리자가 사람마다 켜고 끈다. 서버가 요청마다 거른다.

python -m tests.test_perms   (server/ 에서)
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

from jcc_server import accounts as AC
from jcc_server import app as appmod
from jcc_server.storage import Storage

_fails = []


def check(name, cond, detail=""):
    print(("[PASS] " if cond else "[FAIL] ") + name + (f" — {detail}" if detail else ""))
    if not cond:
        _fails.append(name)


def run() -> int:
    print("=== 기본값 ===")
    check("관리자 기본: 전부", all(AC.effective_perms("manager").values()))
    v = AC.effective_perms("viewer")
    check("보기 전용 기본: 경보 확인·수신자만 꺼짐", not v["ack"] and not v["receivers"] and v["export"] and v["reports"])
    check("JCC 관리자: 전부", all(AC.effective_perms("admin").values()))
    check("경로 → 권한 칸", appmod.perm_of("POST", "/api/alarm/ack") == "ack" and appmod.perm_of("GET", "/api/export/hourly.csv") == "export"
          and appmod.perm_of("GET", "/api/guard") is None)

    fd, db = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    st = Storage(db)
    st.ingest({"device_id": "ccm-1", "panel": "p1", "panel_name": "A동", "readings": [
        {"key": "cabinet_temp", "name": "함내 온도", "unit": "C", "kind": "temp", "value": 30, "ok": True, "ts": time.time()}]})
    cid = st.accounts.create_customer("평택")
    st.accounts.assign_panel("p1", cid)
    uid, temp = st.accounts.create_user("view.p", "viewer", cid)
    srv = appmod.make_server("127.0.0.1", 0, st)
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    def client():
        op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

        def call(path, body=None):
            req = urllib.request.Request(base + path, method="POST" if body is not None else "GET",
                                         data=None if body is None else json.dumps(body).encode(), headers={"Content-Type": "application/json"})
            try:
                with op.open(req, timeout=10) as res:
                    return res.status, json.loads(res.read() or b"{}") if "json" in res.headers.get("Content-Type", "") else {}
            except urllib.error.HTTPError as e:
                try:
                    return e.code, json.loads(e.read() or b"{}")
                except ValueError:
                    return e.code, {}
        return call
    try:
        print("\n=== 요청마다 거름 ===")
        cv = client()
        cv("/api/login", {"user": "view.p", "password": temp})
        cv("/api/me/password", {"old": temp, "new": "view-p-pw-2026"})
        cv("/api/login", {"user": "view.p", "password": "view-p-pw-2026"})
        _, me = cv("/api/auth/status")
        check("화면에 권한이 내려감", me["user"]["perms"]["export"] is True and me["user"]["perms"]["ack"] is False, str(me["user"].get("perms")))
        check("보기 전용 기본: 내보내기 됨", cv("/api/export/events.csv?days=7")[0] == 200)
        c, j = cv("/api/alarm/ack", {"id": 1})
        check("보기 전용 기본: 경보 확인 막힘(권한 이름으로 안내)", c == 403 and "경보 확인" in j.get("error", ""), str(j))
        ca = client()
        ca("/api/login", {"user": "jccops", "password": "env-admin-pw"})
        c, j = ca("/api/admin/user/perms", {"user_id": uid, "perms": {"export": False, "ack": True}})
        check("JCC 관리자가 사람별로 바꿈", c == 200 and j["perms"]["export"] is False and j["perms"]["ack"] is True, str(j))
        with st.accounts._lock:
            raw = st.accounts._conn.execute("SELECT perms FROM users WHERE id=?", (uid,)).fetchone()["perms"]
        check("기본값과 다른 칸만 저장", json.loads(raw) == {"ack": True, "export": False}, raw)
        check("바뀐 권한이 바로 적용: 내보내기 막힘", cv("/api/export/events.csv?days=7")[0] == 403)
        check("바뀐 권한이 바로 적용: 경보 확인 통과(없는 경보라 404/400)", cv("/api/alarm/ack", {"id": 999})[0] in (400, 404))
        c, j = ca("/api/admin/user/perms", {"user_id": uid, "perms": {}})
        check("빈 값 = 기본값으로", c == 200 and j["perms"] == AC.effective_perms("viewer"))
        c, j = cv("/api/admin/user/perms", {"user_id": uid, "perms": {"ack": True}})
        check("고객 계정은 권한을 못 바꿈", c == 403)
    finally:
        srv.shutdown()
        st.close()
        os.unlink(db)
    print("\n" + ("✅ 전체 통과" if not _fails else f"❌ 실패 {len(_fails)}건: {_fails}"))
    return 0 if not _fails else 1


if __name__ == "__main__":
    sys.exit(run())
