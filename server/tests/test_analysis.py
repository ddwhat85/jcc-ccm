"""기록 분석 — 짧은 기간은 원본 5분 칸, 긴 기간은 시간별, 통계·피크·지난 기간 비교, 계정 범위.

python -m tests.test_analysis   (server/ 에서)
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

from jcc_server import analysis as A
from jcc_server import app as appmod
from jcc_server import forecast
from jcc_server.storage import Storage

_fails = []


def check(name, cond, detail=""):
    print(("[PASS] " if cond else "[FAIL] ") + name + (f" — {detail}" if detail else ""))
    if not cond:
        _fails.append(name)


def run() -> int:
    print("=== 계산 ===")
    pts = [(i * 3600.0, v, v - 1, v + 1) for i, v in enumerate([10, 12, 30, 12, 10, 11, 10, 10, 25, 10, 10, 40, 10])]
    pk = A.peaks(pts, n=3, gap=3)
    check("피크: 높은 봉우리, 서로 떨어진 것만", [v for _, v in pk] == [31, 26, 41], str(pk))
    st = A.stats(pts)
    check("통계: 최고는 칸 최고·시각", st["max"] == 41 and st["max_ts"] == 11 * 3600 and st["min"] == 9, str(st))
    check("변화율 = 첫 칸 → 마지막 칸", st["change_pct"] == 0.0)
    check("빈 기록 통계 None", A.stats([]) is None)
    t1 = 1_800_000_000.0
    check("기간: days", A.parse_range({"days": ["30"], "to": [str(t1)]}, t1) == (t1 - 30 * 86400, t1))
    for bad in ({"from": [str(t1)], "to": [str(t1 - 1)]}, {"days": ["4000"]}):
        try:
            A.parse_range(bad, t1)
            check("잘못된 기간은 거부", False, str(bad))
        except ValueError:
            check("잘못된 기간은 거부", True)

    print("\n=== 저장소 ===")
    fd, db = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    s = Storage(db)
    now = time.time()
    try:
        s.ingest({"device_id": "ccm-1", "panel": "p1", "panel_name": "A동", "readings": [
            {"key": "cabinet_temp", "name": "함내 온도", "unit": "C", "kind": "temp", "value": 30 + i * .1, "ok": True, "ts": now - 3600 + i * 60}
            for i in range(60)]})
        s.ingest({"device_id": "ccm-2", "panel": "p2", "panel_name": "B동", "readings": [
            {"key": "cabinet_temp", "name": "함내 온도", "unit": "C", "kind": "temp", "value": 25, "ok": True, "ts": now}]})
        forecast.ensure(s)
        h0 = (now // 3600) * 3600
        with s._lock:
            s._conn.executemany("INSERT OR REPLACE INTO hourly (device_id, sensor_key, hour, avg, vmin, vmax, n) VALUES (?, ?, ?, ?, ?, ?, ?)",
                                [("ccm-1", "cabinet_temp", h0 - k * 3600, 28 + (k % 24) / 10, 27, 29 + (k == 30), 6) for k in range(1, 24 * 14)])
            s._conn.commit()
        r = A.analyze(s, ["ccm-1:cabinet_temp"], now - 3 * 3600, now)
        x = r["sensors"][0]
        check("2일 이하: 원본 5분 칸", x["step"] == 300 and 10 <= len(x["points"]) <= 14 and x["name"] == "함내 온도" and x["panel_name"] == "A동",
              f"{x['step']} {len(x['points'])}")
        r = A.analyze(s, ["ccm-1:cabinet_temp"], now - 7 * 86400, now)
        x = r["sensors"][0]
        check("2일 넘으면 시간별", x["step"] == 3600 and len(x["points"]) > 100 and x["stats"]["max"] == 30, str(x["stats"]))
        check("지난 기간 비교(앞 7일, 지금 기간 위로 옮겨 그림)", x["prev"]["stats"] and all(now - 7 * 86400 <= p[0] < now for p in x["prev"]["points"]),
              str(len(x["prev"]["points"])))

        print("\n=== 계정 범위 ===")
        cid = s.accounts.create_customer("평택")
        s.accounts.assign_panel("p1", cid)
        _, temp = s.accounts.create_user("view.an", "viewer", cid)
        srv = appmod.make_server("127.0.0.1", 0, s)
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
            call("/api/login", {"user": "view.an", "password": temp})
            call("/api/me/password", {"old": temp, "new": "view-an-pw-2026"})
            call("/api/login", {"user": "view.an", "password": "view-an-pw-2026"})
            c, j = call("/api/analysis?ids=ccm-1:cabinet_temp&days=7")
            check("고객: 자기 판넬 분석", c == 200 and j["sensors"][0]["stats"], str(c))
            check("고객: 남의 판넬 403", call("/api/analysis?ids=ccm-2:cabinet_temp&days=7")[0] == 403)
            check("센서 없으면 400", call("/api/analysis?ids=&days=7")[0] == 400)
        finally:
            srv.shutdown()
    finally:
        s.close()
        os.unlink(db)
    print("\n" + ("✅ 전체 통과" if not _fails else f"❌ 실패 {len(_fails)}건: {_fails}"))
    return 0 if not _fails else 1


if __name__ == "__main__":
    sys.exit(run())
