"""온도가 오르는 이유와 할 일 — 기록 모양으로 원인 가르기(가동 부하·주변 온도·냉각 저하), 결로 하한, 점검 요청.

python -m tests.test_thermal   (server/ 에서)
"""
from __future__ import annotations

import json
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
from jcc_server import thermal as T
from jcc_server.storage import Storage

_fails = []


def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not cond:
        _fails.append(name)


def synth(now, days=28, base=30.0, load=0.0, ambient=0.0, peak_trend=0.0, night_trend=0.0, seed=3):
    """load: 평일 9~18시 더하기 · ambient: 매일 낮 더하기 · *_trend: 주마다(최근일수록) 오르는 양."""
    rnd = random.Random(seed)
    h0 = (now // 3600) * 3600
    s = {}
    for i in range(1, days * 24):
        t = h0 - i * 3600
        k = datetime.fromtimestamp(t, T.KST)
        weeks_ago = i / 24 / 7
        day = max(0.0, (12 - abs(k.hour - 14)) / 12) if 8 <= k.hour < 20 else 0.0
        v = base - night_trend * weeks_ago + ambient * day + rnd.gauss(0, 0.15)
        if k.weekday() < 5 and 9 <= k.hour < 18:
            v += load - peak_trend * weeks_ago
        s[t] = v
    return s


def run():
    now = time.time()
    print("=== 원인 가르기 ===")
    a = T.analyze(synth(now, load=4.0, ambient=0.5), {}, now, warn=33.0)
    kinds = [c["kind"] for c in a["causes"]]
    check("평일 가동 시간에만 오름 → 가동 부하", kinds == ["load"], str(kinds))
    check("근거 숫자: 평일 상승 > 주말 상승", a["weekday_rise"] - a["weekend_rise"] >= 3, f"{a['weekday_rise']} / {a['weekend_rise']}")
    check("오르는 시간대 글로", a["hot_hours"].endswith("시") and "~" in a["hot_hours"], a["hot_hours"])
    check("주의 기준을 여러 날 넘으면 '조치 필요'", a["level"] == "act" and a["over_days"] >= 2, str(a["over_days"]))
    check("할 일: 청소 → 설정 점검 → 냉각 용량 검토(요청 버튼)", [x["title"][:4] for x in a["actions"]][0] == "필터·환"
          and any(x.get("request") == "cooling_review" for x in a["actions"]))

    b = T.analyze(synth(now, ambient=3.0), {}, now, warn=40.0)
    check("주말에도 낮에 오름 → 주변 온도", [c["kind"] for c in b["causes"]] == ["ambient"], str([c["kind"] for c in b["causes"]]))
    check("주의 기준과 멀면 할 일 없음", b["level"] == "ok" and b["actions"] == [], b["level"])

    c = T.analyze(synth(now, load=2.0, peak_trend=0.8), {}, now, warn=45.0)
    check("최고 온도만 주마다 오름 → 냉각 저하 의심", "degrade" in [x["kind"] for x in c["causes"]] and c["level"] == "act",
          f"peak {c['peak_trend']} night {c['night_trend']}")
    d = T.analyze(synth(now, load=2.0, peak_trend=0.0, night_trend=0.8), {}, now, warn=45.0)
    check("밤 온도까지 같이 오르면 저하가 아니라 주변(계절)", "degrade" not in [x["kind"] for x in d["causes"]]
          and "ambient" in [x["kind"] for x in d["causes"]], str([x["kind"] for x in d["causes"]]))

    e = T.analyze(synth(now, days=10, load=4), {}, now, warn=33)
    check("기록 14일 미만이면 판단 안 함", e["building"] and not e["causes"] and not e["actions"])

    # 결로 하한: 30℃·60%면 이슬점 약 21.4℃ → 하한 25℃
    temp = synth(now, base=30.0, load=4.0)
    hum = {t: 60.0 for t in temp}
    f = T.analyze(temp, hum, now, warn=32)
    check("공조 설정 하한 = 이슬점 + 3℃(올림)", f.get("dew_point") and f["setpoint_floor"] == int(-(-(f["dew_point"] + 3) // 1))
          and f["dew_point"] > 20, f"dp={f.get('dew_point')} floor={f.get('setpoint_floor')}")
    check("설정 점검 문구에 하한·결로", any("결로" in x["why"] and str(f["setpoint_floor"]) in x["why"] for x in f["actions"]))

    print("\n=== 저장소·화면 경로 ===")
    fd, db = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    st = Storage(db)
    for dev, pnl, nm in (("ccm-1", "p1", "A동"), ("ccm-2", "p2", "B동")):
        st.ingest({"device_id": dev, "panel": pnl, "panel_name": nm, "readings": [
            {"key": "cabinet_temp", "name": "함내 온도", "unit": "C", "kind": "temp", "value": 30, "ok": True, "ts": now},
            {"key": "cabinet_humidity", "name": "함내 습도", "unit": "%RH", "kind": "humidity", "value": 55, "ok": True, "ts": now}]})
    from jcc_server import forecast as F
    F.ensure(st)
    hot = synth(now, base=30.0, load=6.0)
    with st._lock:
        st._conn.executemany("INSERT OR REPLACE INTO hourly (device_id, sensor_key, hour, avg) VALUES (?, ?, ?, ?)",
                             [("ccm-1", "cabinet_temp", t, v) for t, v in hot.items()] +
                             [("ccm-1", "cabinet_humidity", t, 55.0) for t in hot] +
                             [("ccm-2", "cabinet_temp", t, 25.0) for t in hot])
        st._conn.execute("INSERT OR REPLACE INTO settings (device_id, sensor_key, alarm_warn, updated_at) VALUES ('ccm-1', 'cabinet_temp', 34, ?)", (now,))
        st._conn.commit()
    pt = T.panel_thermal(st, "p1", now)
    check("판넬: 주의 기준(설정값)·원인·결로 하한", pt and pt["warn"] == 34 and pt["level"] == "act" and pt.get("setpoint_floor"),
          str(pt and (pt["warn"], pt["level"], pt.get("setpoint_floor"))))
    check("조용한 판넬은 후보에 없음", [c["panel"] for c in T.candidates(st, now)] == ["p1"])

    cid = st.accounts.create_customer("평택")
    st.accounts.assign_panel("p1", cid)
    _, temp_pw = st.accounts.create_user("view.t", "viewer", cid)
    srv = appmod.make_server("127.0.0.1", 0, st)
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    import http.cookiejar

    def client():
        op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

        def call(path, body=None):
            req = urllib.request.Request(base + path, method="POST" if body is not None else "GET",
                                         data=None if body is None else json.dumps(body).encode(), headers={"Content-Type": "application/json"})
            try:
                with op.open(req, timeout=10) as r:
                    return r.status, json.loads(r.read() or b"{}")
            except urllib.error.HTTPError as e:
                return e.code, {}
        return call
    try:
        cu = client()
        cu("/api/login", {"user": "view.t", "password": temp_pw})
        cu("/api/me/password", {"old": temp_pw, "new": "view-t-pw-2026"})
        cu("/api/login", {"user": "view.t", "password": "view-t-pw-2026"})
        s, j = cu("/api/guard/thermal?panel=p1")
        check("고객: 자기 판넬 원인·할 일", s == 200 and j["causes"] and j["actions"], str(s))
        check("고객: 남의 판넬 404", cu("/api/guard/thermal?panel=p2")[0] == 404)
        s, j = cu("/api/guard/request", {"panel": "p1", "kind": "cooling_review"})
        check("고객: 냉각 점검 요청", s == 200 and j["ok"] and j["existing"] is False, str(j))
        s, j = cu("/api/guard/request", {"panel": "p1", "kind": "cooling_review"})
        check("같은 판넬 열린 요청이 있으면 새로 안 만듦", s == 200 and j["existing"] is True)
        check("남의 판넬엔 요청 못 함", cu("/api/guard/request", {"panel": "p2", "kind": "cooling_review"})[0] == 404)
        check("모르는 요청 종류 400", cu("/api/guard/request", {"panel": "p1", "kind": "x"})[0] == 400)
        check("고객은 운영자 후보 목록 못 봄", cu("/api/admin/thermal")[0] == 403)
        adm = client()
        adm("/api/login", {"user": "jccops", "password": "env-admin-pw"})
        s, j = adm("/api/admin/thermal")
        p1 = next((x for x in j.get("panels", []) if x["panel"] == "p1"), None)
        check("운영자: 공조 점검 후보에 요청·고객사", s == 200 and p1 and p1["request"] and p1["customer"] == "평택", str(p1)[:160])
        s, _ = adm("/api/admin/request_done", {"request_id": p1["request"]["id"]})
        check("운영자: 요청 처리 완료", s == 200 and T.open_request(st, "p1") is None)
        check("이미 처리한 요청 400", adm("/api/admin/request_done", {"request_id": p1["request"]["id"]})[0] == 400)
    finally:
        srv.shutdown()
        st.close()
        os.unlink(db)
    print("\n" + ("✅ 전체 통과" if not _fails else f"❌ 실패 {len(_fails)}건: {_fails}"))
    return 0 if not _fails else 1


if __name__ == "__main__":
    sys.exit(run())
