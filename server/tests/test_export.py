"""CSV 내보내기 — 엑셀용 BOM·열·한국 시간·원인/메모, 고객 범위, 센서 이력 검사.

python -m tests.test_export   (server/ 에서)
"""
from __future__ import annotations

import csv
import http.cookiejar
import io
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
from jcc_server import export
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
    for dev, panel in (("ccm-a", "pa"), ("ccm-b", "pb")):
        st.ingest({"device_id": dev, "panel": panel, "panel_name": panel.upper(), "readings": [
            {"key": "t", "name": "온도", "unit": "C", "value": 20 + k, "ok": True, "ts": now - 600 + k * 60} for k in range(5)]})
        st.raise_alarm(dev, "t", "alarm", f"{dev} 온도, 위험 \"높음\"")
    aid = next(a["id"] for a in st.list_active_alarms() if a["device_id"] == "ccm-a")
    st.ack_alarm(aid, "kim", "팬 필터 교체", "real")
    cid = st.accounts.create_customer("A")
    st.accounts.assign_panel("pa", cid)
    uid, temp = st.accounts.create_user("kim.a", "viewer", cid)
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
        adm = client()
        adm("/api/login", {"user": "jccops", "password": "env-admin-pw"})
        s, raw, h = adm("/api/export/alarms.csv?days=7")
        rows = list(csv.reader(io.StringIO(raw.decode("utf-8-sig"))))
        check("경보 CSV: BOM·첨부 파일·한글 머리글", s == 200 and raw.startswith(b"\xef\xbb\xbf") and "attachment" in h.get("Content-Disposition", "")
              and rows[0][:3] == ["경보번호", "판넬", "기기"], str(rows[0][:3]))
        body = {r[2]: r for r in rows[1:]}
        check("두 판넬 경보·쉼표·따옴표 안전", set(body) == {"ccm-a", "ccm-b"} and body["ccm-a"][6] == 'ccm-a 온도, 위험 "높음"')
        check("원인·조치 메모·한국 시간", body["ccm-a"][10] == "실제 이상" and body["ccm-a"][11] == "팬 필터 교체"
              and len(body["ccm-a"][7]) == 19 and body["ccm-a"][7][4] == "-")
        s, raw, h = adm("/api/export/readings.csv?days=1&device_id=ccm-a&sensor=t")
        rows = list(csv.reader(io.StringIO(raw.decode("utf-8-sig"))))
        check("센서 이력 CSV 5행·오래된 순", s == 200 and len(rows) == 6 and rows[1][4] == "20.0" and rows[5][4] == "24.0", str(rows[1:3]))
        check("센서 지정 없으면 400", adm("/api/export/readings.csv")[0] == 400)
        v = client()
        v("/api/login", {"user": "kim.a", "password": temp})
        v("/api/me/password", {"old": temp, "new": "viewer-pw-20261"})
        v("/api/login", {"user": "kim.a", "password": "viewer-pw-20261"})
        s, raw, _ = v("/api/export/alarms.csv")
        rows = list(csv.reader(io.StringIO(raw.decode("utf-8-sig"))))
        check("고객: 자기 판넬 경보만", s == 200 and [r[2] for r in rows[1:]] == ["ccm-a"], str([r[2] for r in rows[1:]]))
        check("고객: 남의 센서 이력 403", v("/api/export/readings.csv?device_id=ccm-b&sensor=t")[0] == 403)
        check("로그인 전 401", client()("/api/export/alarms.csv")[0] == 401)
        # 상한: 경보는 500건에서 조용히 잘리면 안 되고, 센서 이력은 넘치면 최근 값을 남긴다
        with st._lock:
            for i in range(700):
                st._conn.execute("INSERT INTO alarms (device_id, sensor_key, kind, detail, severity, raised_at, cleared_at) "
                                 "VALUES ('ccm-b','t','alarm','x','warn',?,?)", (now - 3600 + i, now - 3600 + i + 1))
            st._conn.commit()
        rows = list(csv.reader(io.StringIO(export.alarms_csv(st, 7).decode("utf-8-sig"))))
        check("경보 CSV 500건 넘어도 전부", len(rows) - 1 == 702, str(len(rows) - 1))
        old, export.MAX_ROWS = export.MAX_ROWS, 3
        try:
            rows = list(csv.reader(io.StringIO(export.readings_csv(st, "ccm-a", "t", 1).decode("utf-8-sig"))))
        finally:
            export.MAX_ROWS = old
        check("센서 이력 상한 넘치면 최근 값 남김(시간순)", [r[4] for r in rows[1:]] == ["22.0", "23.0", "24.0"], str(rows[1:]))
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
