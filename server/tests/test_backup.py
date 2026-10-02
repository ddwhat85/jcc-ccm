"""데이터 자동 백업 — 사본이 진짜 데이터베이스인지·하루 한 번·보관 일수·운영자만·경로 탈출 차단.

python -m tests.test_backup   (server/ 에서)
"""
from __future__ import annotations

import http.cookiejar
import json
import os
import shutil
import sqlite3
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
from jcc_server import backup
from jcc_server.storage import Storage

_fails = []


def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not cond:
        _fails.append(name)


def run():
    tmpd = tempfile.mkdtemp()
    os.environ["JCC_BACKUP_DIR"] = os.path.join(tmpd, "bk")
    os.environ["JCC_BACKUP_KEEP"] = "3"
    st = Storage(os.path.join(tmpd, "jcc.db"))
    now = time.time()
    st.ingest({"device_id": "ccm-1", "panel": "p1", "panel_name": "1번", "readings": [
        {"key": "t", "name": "온도", "unit": "C", "value": 25, "ok": True, "ts": now}]})

    made = backup.daily(st, now)
    check("오늘 자동 사본 생김", made is not None and made["name"].startswith("jcc-") and made["size"] > 0, str(made))
    check("같은 날 다시 불러도 안 만듦", backup.daily(st, now + 60) is None)
    con = sqlite3.connect(os.path.join(os.environ["JCC_BACKUP_DIR"], made["name"]))
    n = con.execute("SELECT COUNT(*) FROM readings").fetchone()[0]
    con.close()
    check("사본은 열리는 데이터베이스이고 읽기값이 들어 있음", n >= 1, str(n))
    for d in range(1, 6):                      # 5일 치 더 → 보관 3일만 남아야
        backup.daily(st, now + d * 86400)
    days = {b["name"][4:12] for b in backup.list_backups(st)}
    check("보관 일수(3일)만 남김", len(days) == 3, str(sorted(days)))
    m = backup.make_backup(st, now + 6 * 86400, manual=True)
    check("지금 백업은 시각까지 붙은 이름", "-" in m["name"][4:] and backup.path_for(st, m["name"]))
    check("이상한 이름은 경로 없음", backup.path_for(st, "../jcc.db") is None and backup.path_for(st, "jcc-1.db") is None)

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
                    return r.status, r.read(), dict(r.headers)
            except urllib.error.HTTPError as e:
                return e.code, e.read(), dict(e.headers)
        return call

    try:
        anon = client()
        check("로그인 전 목록 401", anon("/api/admin/backups")[0] == 401)
        adm = client()
        adm("/api/login", {"user": "jccops", "password": "env-admin-pw"})
        s, raw, _ = adm("/api/admin/backups")
        lst = json.loads(raw)["backups"]
        check("운영자: 목록", s == 200 and len(lst) >= 3, str(s))
        s, raw, h = adm("/api/admin/backup/" + lst[0]["name"])
        check("운영자: 내려받기(첨부·SQLite 파일)", s == 200 and raw[:16] == b"SQLite format 3\x00"
              and "attachment" in h.get("Content-Disposition", ""), str(s))
        check("운영자: 없는 사본 404", adm("/api/admin/backup/jcc-20000101.db")[0] == 404)
        check("경로 탈출 막힘", adm("/api/admin/backup/..%2Fjcc.db")[0] in (403, 404))
        s, raw, _ = adm("/api/admin/backup_now", {})
        check("운영자: 지금 백업", s == 200 and json.loads(raw)["ok"] is True)
        cid = st.accounts.create_customer("A")
        _, temp = st.accounts.create_user("mgr.b", "manager", cid)
        cu = client()
        cu("/api/login", {"user": "mgr.b", "password": temp})
        cu("/api/me/password", {"old": temp, "new": "mgr-b-pw-20261"})
        cu("/api/login", {"user": "mgr.b", "password": "mgr-b-pw-20261"})
        check("고객 계정은 백업 목록 403", cu("/api/admin/backups")[0] == 403)
        check("고객 계정은 내려받기 403", cu("/api/admin/backup/" + lst[0]["name"])[0] == 403)
        check("고객 계정은 지금 백업 403", cu("/api/admin/backup_now", {})[0] == 403)
    finally:
        srv.shutdown()
        st.close()
        os.environ.pop("JCC_BACKUP_DIR", None)
        os.environ.pop("JCC_BACKUP_KEEP", None)
        shutil.rmtree(tmpd, ignore_errors=True)
    print()
    if _fails:
        print(f"❌ {len(_fails)} FAIL: {_fails}")
        return 1
    print("✅ 전체 통과")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
