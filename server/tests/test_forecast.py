"""판넬 온도 예측 — 시간별 평균 쌓기, 내일 예측·범위·평균 오차, 기록이 적으면 예측 안 함, 고객 범위.

python -m tests.test_forecast   (server/ 에서)
"""
from __future__ import annotations

import json
import math
import os
import random
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime

os.environ["JCC_DASHBOARD_PW"] = "env-admin-pw"
os.environ["JCC_DASHBOARD_USER"] = "jccops"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from jcc_server import app as appmod
from jcc_server import forecast as F
from jcc_server.storage import Storage

_fails = []


def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not cond:
        _fails.append(name)


def synth(now: float, days: int, seed=1) -> dict:
    """가상 판넬: 계절(여름 높음) + 낮 시간 상승 + 평일 가동 열 + 잡음."""
    rnd = random.Random(seed)
    h0 = (now // 3600) * 3600
    s = {}
    for i in range(1, days * 24):
        t = h0 - i * 3600
        d = datetime.fromtimestamp(t, F.KST)
        s[t] = (27 + 3 * math.sin(2 * math.pi * (d.timetuple().tm_yday - 110) / 365)
                + 2.5 * max(0.0, math.sin(math.pi * (d.hour - 7) / 12))
                + (1.5 if d.weekday() < 5 and 8 <= d.hour < 19 else 0) + rnd.gauss(0, 0.3))
    return s


def run():
    now = time.time()
    print("=== 예측 계산 ===")
    s = synth(now, 400)
    o = F.build(s, now, warn=33.0)
    tm0 = F._day0(now) + 86400
    tom = [f for f in o["forecast"] if f["ts"] >= tm0]
    check("내일 24시간 예측", len(tom) == 24, str(len(tom)))
    check("지난 30일 실제와 비교한 평균 오차·범위", o["mae"] is not None and o["mae"] < 1.0 and o["band"] and o["tested_days"] == 30,
          f"mae={o['mae']} band={o['band']}")
    check("예측 범위 = 예측 ± 지난 오차", all(f["lo"] < f["v"] < f["hi"] for f in tom))
    pk_h = datetime.fromtimestamp(o["peak"]["ts"], F.KST).hour
    check("가장 높을 때는 오후(패턴대로)", 11 <= pk_h <= 17, str(pk_h))
    check("작년 같은 날 24시간", len(o["last_year"]) == 24)
    check("어제 실제 24시간", len(o["yesterday"]) == 24)
    check("주의 기준 가까우면 알림 표시", o["near_warn"] is True)
    check("주의 기준이 멀면 표시 안 함", F.build(s, now, warn=60.0)["near_warn"] is False)
    # 진짜로 맞는지: 마지막 하루를 가리고 예측 → 실제와 비교
    d0 = F._day0(now) - 86400
    hidden = {t: v for t, v in s.items() if t < d0}
    pr = F.predict(hidden, d0, [d0 + i * 3600 for i in range(24)])
    err = sum(abs(pr[t] - s[t]) for t in pr if t in s) / 24
    check("가린 어제를 예측하면 실제와 평균 1℃ 안", err < 1.0, f"{err:.2f}")

    short = synth(now, 4)
    o2 = F.build(short, now, warn=38)
    check("기록 7일 미만이면 예측 안 함(기록 쌓이는 중)", o2["building"] and not o2["forecast"] and o2["mae"] is None, str(o2["days"]))
    o3 = F.build(synth(now, 9), now, warn=38)
    check("기록이 짧아도 3일 이상 재 봤을 때만 범위", (o3["band"] is None) == (o3["tested_days"] < 3), str(o3["tested_days"]))
    check("빈 기록", F.build({}, now)["building"] is True)

    print("\n=== 시간별 평균 쌓기 ===")
    fd, db = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    st = Storage(db)
    h = (now // 3600) * 3600 - 2 * 3600       # 두 시간 전 정시
    st.ingest({"device_id": "ccm-1", "panel": "p1", "panel_name": "A동", "readings": [
        {"key": "cabinet_temp", "name": "함내 온도", "unit": "C", "kind": "temp", "value": 30.0, "ok": True, "ts": h + 60},
        {"key": "cabinet_humidity", "name": "함내 습도", "unit": "%RH", "kind": "humidity", "value": 50, "ok": True, "ts": h + 60},
        {"key": "h2_lel", "name": "수소", "unit": "%LEL", "kind": "h2", "value": 1, "ok": True, "ts": h + 60}]})
    st.ingest({"device_id": "ccm-1", "panel": "p1", "panel_name": "A동", "readings": [
        {"key": "cabinet_temp", "name": "함내 온도", "unit": "C", "kind": "temp", "value": 32.0, "ok": True, "ts": h + 1800}]})
    st.ingest({"device_id": "ccm-2", "panel": "p2", "panel_name": "B동", "readings": [
        {"key": "cabinet_temp", "name": "함내 온도", "unit": "C", "kind": "temp", "value": 25.0, "ok": True, "ts": now}]})
    F.rollup(st, now)
    ser = F.series(st, "ccm-1", "cabinet_temp", 0)
    check("한 시간 평균", ser.get(h) == 31.0, str(ser))
    check("습도도 쌓고, 가스 센서는 안 쌓음", F.series(st, "ccm-1", "cabinet_humidity", 0) and not F.series(st, "ccm-1", "h2_lel", 0))
    check("아직 안 끝난 시간은 안 쌓음", not F.series(st, "ccm-2", "cabinet_temp", 0))
    F.rollup(st, now)
    check("다시 돌려도 같은 값(덮어씀)", F.series(st, "ccm-1", "cabinet_temp", 0).get(h) == 31.0)
    # 30일치 시간별 평균을 넣고 판넬 예측
    with st._lock:
        st._conn.executemany("INSERT OR REPLACE INTO hourly (device_id, sensor_key, hour, avg, vmin, vmax, n) VALUES (?, ?, ?, ?, ?, ?, ?)",
                             [("ccm-1", "cabinet_temp", t, v, v, v, 6) for t, v in synth(now, 30).items()])
        st._conn.commit()
    pf = F.panel_forecast(st, "p1", now)
    check("판넬 예측: 함내 온도·내일", pf and pf["sensor_name"] == "함내 온도" and len([f for f in pf["forecast"] if f["ts"] >= tm0]) == 24,
          str(pf and pf.get("days")))
    check("없는 판넬은 None", F.panel_forecast(st, "zz", now) is None)
    with st._lock:   # 제품 기본: 주의 38·위험 45 / 운영자는 '위험'만 50으로 → 주의선은 그대로 38
        st._conn.execute("INSERT OR REPLACE INTO discovered (device_id, sensor_key, name, unit, kind, alarm_warn, alarm_max) "
                         "VALUES ('ccm-1', 'cabinet_temp', '함내 온도', 'C', 'temp', 38, 45)")
        st._conn.execute("INSERT OR REPLACE INTO settings (device_id, sensor_key, alarm_max, updated_at) VALUES ('ccm-1', 'cabinet_temp', 50, ?)", (now,))
        st._conn.commit()
    check("운영자가 '위험'만 바꿔도 주의선은 제품 기본 '주의'", F.panel_forecast_source(st, "p1")[3] == 38, str(F.panel_forecast_source(st, "p1")))
    with st._lock:
        st._conn.execute("UPDATE settings SET alarm_warn=36 WHERE device_id='ccm-1'")
        st._conn.commit()
    check("운영자가 '주의'를 바꾸면 그것", F.panel_forecast_source(st, "p1")[3] == 36)
    with st._lock:
        st._conn.execute("INSERT INTO hourly (device_id, sensor_key, hour, avg) VALUES ('ccm-1', 'cabinet_temp', ?, 1)", (now - 2000 * 86400,))
        st._conn.commit()
    F.prune(st, now)
    check("3년 넘은 시간별 평균은 정리", min(F.series(st, "ccm-1", "cabinet_temp", 0)) > now - 1200 * 86400)

    print("\n=== 고객 범위 ===")
    cid = st.accounts.create_customer("평택")
    st.accounts.assign_panel("p1", cid)
    _, temp = st.accounts.create_user("view.f", "viewer", cid)
    srv = appmod.make_server("127.0.0.1", 0, st)
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    import http.cookiejar
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def call(path, body=None):
        req = urllib.request.Request(base + path, method="POST" if body is not None else "GET",
                                     data=None if body is None else json.dumps(body).encode(), headers={"Content-Type": "application/json"})
        try:
            with op.open(req, timeout=10) as r:
                return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            return e.code, {}
    try:
        call("/api/login", {"user": "view.f", "password": temp})
        call("/api/me/password", {"old": temp, "new": "view-f-pw-2026"})
        call("/api/login", {"user": "view.f", "password": "view-f-pw-2026"})
        s1, j = call("/api/guard/forecast?panel=p1")
        check("고객: 자기 판넬 예측", s1 == 200 and j.get("forecast"), str(s1))
        check("고객: 남의 판넬 예측 404", call("/api/guard/forecast?panel=p2")[0] == 404)
    finally:
        srv.shutdown()
        st.close()
        os.unlink(db)
    print("\n" + ("✅ 전체 통과" if not _fails else f"❌ 실패 {len(_fails)}건: {_fails}"))
    return 0 if not _fails else 1


if __name__ == "__main__":
    sys.exit(run())
