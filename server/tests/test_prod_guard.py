"""운영 서버 잠금(JCC_REQUIRE_AUTH=1) — 비번·기기 키를 깜빡해도 '누구나 열림'이 되지 않는다.

python -m tests.test_prod_guard   (server/ 에서)
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import urllib.error
import urllib.request

os.environ["JCC_REQUIRE_AUTH"] = "1"
for k in ("JCC_DASHBOARD_PW", "JCC_DASHBOARD_USER", "JCC_API_KEY"):
    os.environ.pop(k, None)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import importlib

from jcc_server import app as appmod
from jcc_server.storage import Storage

_fails = []


def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not cond:
        _fails.append(name)


def serve(mod):
    fd, db = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    st = Storage(db)
    srv = mod.make_server("127.0.0.1", 0, st)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"

    def call(path, body=None, headers=None):
        req = urllib.request.Request(base + path, method="POST" if body is not None else "GET",
                                     data=None if body is None else json.dumps(body).encode(),
                                     headers=dict({"Content-Type": "application/json"}, **(headers or {})))
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read().decode("utf-8") or "{}")
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode("utf-8") or "{}")
    return srv, st, db, call


def run():
    print("=== 비번·기기 키 모두 빠뜨린 운영 서버 ===")
    srv, st, db, call = serve(appmod)
    try:
        s, j = call("/api/auth/status")
        check("인증 켜짐으로 표시", j.get("enabled") is True and j.get("authed") is False, str(j))
        check("데이터 API 401(누구나 열림이 아님)", call("/api/panels")[0] == 401 and call("/api/devices")[0] == 401)
        check("아무 계정으로도 로그인 안 됨", call("/api/login", {"user": "admin", "password": "admin"})[0] == 401)
        s, j = call("/v1/telemetry", {"device_id": "x", "readings": []})
        check("텔레메트리 거부 + 설정 안내(503)", s == 503 and "JCC_API_KEY" in j.get("error", ""), str(j))
        check("화면 껍데기·상태는 열림", call("/health")[0] == 200)
    finally:
        srv.shutdown()
        st.close()
        os.unlink(db)

    print("\n=== 기기 키 설정 ===")
    os.environ["JCC_API_KEY"] = "dev-key-test-123"
    mod = importlib.reload(appmod)
    srv, st, db, call = serve(mod)
    try:
        body = {"device_id": "ccm-1", "readings": []}
        check("키 없이 401", call("/v1/telemetry", body)[0] == 401)
        check("틀린 키 401", call("/v1/telemetry", body, {"Authorization": "Bearer wrong"})[0] == 401)
        check("맞는 키 200", call("/v1/telemetry", body, {"Authorization": "Bearer dev-key-test-123"})[0] == 200)
        check("OTA도 키 필요", call("/ota/x.tar.gz")[0] == 401)
    finally:
        srv.shutdown()
        st.close()
        os.unlink(db)
        os.environ.pop("JCC_API_KEY", None)
        os.environ.pop("JCC_REQUIRE_AUTH", None)
        importlib.reload(appmod)

    print()
    if _fails:
        print(f"❌ {len(_fails)} FAIL: {_fails}")
        return 1
    print("✅ 전체 통과")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
