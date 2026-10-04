"""에어컨 통신 연결(이더넷·Modbus TCP, 읽기 전용) — 레지스터 표, 연결·끊기, 지금 상태·가동률, 실측 원인, 고착 오판 방지.

python -m tests.test_aircon   (server/ 에서)
"""
from __future__ import annotations

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

from jcc_server import aircon as AC
from jcc_server import app as appmod
from jcc_server import equipment as E
from jcc_server import forecast as F
from jcc_server import manual_sensors as MS
from jcc_server import thermal as T
from jcc_server.storage import Storage

_fails = []


def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not cond:
        _fails.append(name)


ITEMS = {"temp": {"register": 100, "type": "holding", "datatype": "int16", "scale": 0.1},
         "setpoint": {"register": 101, "type": "holding", "datatype": "int16", "scale": 0.1},
         "run": {"register": 110, "type": "holding", "datatype": "uint16"},
         "alarm": {"register": 120, "type": "holding", "datatype": "uint16"}}


def run():
    fd, db = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    st = Storage(db)
    now = time.time()
    for dev, pnl in (("ccm-1", "p1"), ("ccm-9", "p9")):
        st.ingest({"device_id": dev, "panel": pnl, "panel_name": pnl.upper(), "readings": [
            {"key": "cabinet_temp", "name": "함내 온도", "unit": "C", "kind": "temp", "value": 30, "ok": True, "ts": now}]})

    with st._lock:   # 실제 CCM처럼 자동 탐색으로 함내 온도가 잡혀 있는 상태
        for dev in ("ccm-1", "ccm-9"):
            st._conn.execute("INSERT INTO discovered (device_id, sensor_key, name, unit, kind, source, confidence, enabled, discovered_at) "
                             "VALUES (?, 'cabinet_temp', '함내 온도', 'C', 'temp', 'builtin', '확인', 1, ?)", (dev, now))
        st._conn.commit()
    print("=== 레지스터 표 ===")
    for bad, why in (({"run": ITEMS["run"]}, "내부 온도"), ({"temp": ITEMS["temp"]}, "압축기 가동"),
                     ({"temp": dict(ITEMS["temp"], datatype="float64"), "run": ITEMS["run"]}, "형식")):
        try:
            AC.clean_profile("X", bad)
            check(f"레지스터 표 검사: {why}", False)
        except ValueError as exc:
            check(f"레지스터 표 검사: {why}", why in str(exc) or "형식" in str(exc), str(exc))
    p = AC.save_profile(st, "리탈 Blue e+ (예시)", ITEMS, "jccops")
    check("레지스터 표 저장·불러오기", [x["name"] for x in AC.profiles(st)] == ["리탈 Blue e+ (예시)"] and set(p["items"]) == set(ITEMS))

    print("\n=== 연결 ===")
    E.save(st, "p1", {"width": 800, "height": 2000, "depth": 600, "target_c": 35, "ambient_c": 35, "heat": [{"loss_w": 900, "qty": 1}],
                      "equipment": [{"kind": "aircon", "name": "측면 에어컨", "capacity_w": 1000, "qty": 1},
                                    {"kind": "fan_filter", "name": "팬필터", "airflow_m3h": 100, "qty": 1},
                                    {"kind": "aircon", "name": "도어 에어컨", "capacity_w": 500, "qty": 1}]}, "t")
    for args, why in (((5, "ccm-1"), "없는 장치"), ((1, "ccm-1"), "에어컨"), ((0, "ccm-9"), "이 판넬의 CCM")):
        try:
            AC.link(st, "p1", args[0], args[1], "192.168.1.60", 502, 1, "리탈 Blue e+ (예시)", "t")
            check(f"잘못된 연결 거부: {why}", False)
        except ValueError as exc:
            check(f"잘못된 연결 거부: {why}", True, str(exc))
    lk = AC.link(st, "p1", 0, "ccm-1", "192.168.1.60", 502, 1, "리탈 Blue e+ (예시)", "t")
    keys = sorted(s["key"] for s in MS.specs_of(st, "ccm-1"))
    check("연결 → CCM에 직접 지정 센서(TCP) 4개", keys == ["ac1_alarm", "ac1_run", "ac1_setpoint", "ac1_temp"]
          and all(s["driver"] == "modbus_tcp" and s["host"] == "192.168.1.60" for s in MS.specs_of(st, "ccm-1")), str(keys))
    check("연결 정보가 판넬 설비에 남음", E.get(st, "p1")["equipment"][0]["link"]["tag"] == "ac1")
    lk2 = AC.link(st, "p1", 2, "ccm-1", "192.168.1.61", 502, 1, "리탈 Blue e+ (예시)", "t")
    check("두 번째 에어컨은 ac2", lk2["tag"] == "ac2" and len(MS.specs_of(st, "ccm-1")) == 8)
    AC.link(st, "p1", 0, "ccm-1", "192.168.1.70", 502, 3, "리탈 Blue e+ (예시)", "t")
    check("같은 에어컨 다시 연결은 같은 번호", E.get(st, "p1")["equipment"][0]["link"]["tag"] == "ac1")
    specs = MS.specs_of(st, "ccm-1")
    check("다시 연결하면 바꿔 끼움(센서 수 그대로·새 IP)", len(specs) == 8 and any(s["host"] == "192.168.1.70" for s in specs)
          and not any(s["host"] == "192.168.1.60" for s in specs))
    alarm = next(r for r in st._conn.execute("SELECT * FROM discovered WHERE device_id='ccm-1'") if r["sensor_key"].endswith("_alarm"))
    check("알람 레지스터는 0이 아니면 위험", alarm["alarm_max"] == 0.5 and alarm["kind"] == "aircon_alarm")

    print("\n=== 지금 상태·가동률 ===")
    tag = E.get(st, "p1")["equipment"][0]["link"]["tag"]
    st.ingest({"device_id": "ccm-1", "panel": "p1", "panel_name": "P1", "readings": [
        {"key": f"{tag}_temp", "name": "t", "unit": "C", "value": 33.5, "ok": True, "ts": now},
        {"key": f"{tag}_setpoint", "name": "s", "unit": "C", "value": 35, "ok": True, "ts": now},
        {"key": f"{tag}_run", "name": "r", "unit": "", "value": 1, "ok": True, "ts": now},
        {"key": f"{tag}_alarm", "name": "a", "unit": "", "value": 0, "ok": True, "ts": now}]})
    F.ensure(st)
    from tests.test_thermal import synth
    hot = synth(now, base=30.0, load=6.0)
    with st._lock:
        st._conn.executemany("INSERT OR REPLACE INTO hourly (device_id, sensor_key, hour, avg) VALUES (?, ?, ?, ?)",
                             [("ccm-1", "cabinet_temp", t, v) for t, v in hot.items()] +
                             [("ccm-1", f"{tag}_run", t, 1.0) for t in hot])
        st._conn.execute("INSERT OR REPLACE INTO settings (device_id, sensor_key, alarm_warn, updated_at) VALUES ('ccm-1', 'cabinet_temp', 34, ?)", (now,))
        st._conn.commit()
    u = AC.panel_status(st, "p1", now)[0]
    check("지금 값: 가동·설정·에어컨이 잰 온도·알람 없음", u["running"] is True and u["setpoint"] == 35 and u["temp"] == 33.5 and u["alarm"] is False, str(u))
    check("CCM 반영 전이면 '반영 대기'", u["state"] == "pending")
    MS.note_report(st, "ccm-1", {"version": MS.version_of(st, "ccm-1"), "status": "ok",
                                 "active": [s["key"] for s in MS.specs_of(st, "ccm-1")], "errors": {}})
    check("CCM이 적용하고 값이 오면 '연결됨'", AC.panel_status(st, "p1", now)[0]["state"] == "live")
    check("가동률(더운 시간)", AC.panel_status(st, "p1", now)[0]["duty_hot"] == 1.0)

    print("\n=== 실측 원인 ===")
    th = T.panel_thermal(st, "p1", now)
    check("더운 시간 100% 가동인데 기준 넘음 → 용량 부족 실측", th["causes"][0]["kind"] == "measured_short", str([c["kind"] for c in th["causes"]]))
    with st._lock:
        st._conn.execute(f"UPDATE hourly SET avg=0.3 WHERE sensor_key='{tag}_run'")
        st._conn.commit()
    th = T.panel_thermal(st, "p1", now)
    check("덜 도는데 기준 넘음 → '에어컨이 덜 돌고 있음'", th["causes"][0]["kind"] == "underused", str([c["kind"] for c in th["causes"]]))
    st.ingest({"device_id": "ccm-1", "panel": "p1", "panel_name": "P1", "readings": [
        {"key": f"{tag}_alarm", "name": "a", "unit": "", "value": 1, "ok": True, "ts": now + 1}]})
    th = T.panel_thermal(st, "p1", now + 2)
    check("에어컨 알람이면 할 일 맨 위 '에어컨 알람 확인'", th["actions"][0]["title"] == "에어컨 알람 확인")
    check("에어컨 알람은 위험 경보로", any(a["sensor_key"] == f"{tag}_alarm" and a["severity"] == "crit" for a in st.list_active_alarms()))

    print("\n=== 고착 오판·온도 착각 방지 ===")
    for i in range(40):
        st.ingest({"device_id": "ccm-1", "panel": "p1", "panel_name": "P1", "readings": [
            {"key": f"{tag}_setpoint", "name": "s", "unit": "C", "value": 35, "ok": True, "ts": now + 10 + i * 30}]})
    st.health_scan(sensor_timeout=1e9)
    check("설정 온도가 며칠 그대로여도 '센서 고착'이 아님", not any(e["etype"] == "stuck" and e["sensor_key"] == f"{tag}_setpoint"
                                                     for e in st.events_since(0, None, 500)))
    st.ingest({"device_id": "ccm-5", "panel": "p5", "panel_name": "P5", "readings": [
        {"key": "ac1_temp", "name": "에어컨 내부 온도", "unit": "C", "value": 30, "ok": True, "ts": now}]})
    check("에어컨이 잰 온도만 있는 판넬은 판넬 온도로 착각하지 않음", F.panel_forecast_source(st, "p5") is None)

    print("\n=== 화면 경로 ===")
    cid = st.accounts.create_customer("평택")
    st.accounts.assign_panel("p1", cid)
    _, tpw = st.accounts.create_user("view.a", "viewer", cid)
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
        adm = client()
        adm("/api/login", {"user": "jccops", "password": "env-admin-pw"})
        s, j = adm("/api/admin/aircon_profiles")
        check("운영자: 레지스터 표 목록·항목", s == 200 and j["profiles"] and [i["key"] for i in j["items"]][:3] == ["temp", "setpoint", "run"])
        s, j = adm("/api/admin/aircon_profile", {"name": "다른 기종", "items": {"temp": {"register": 1}, "run": {"register": 2}}})
        check("운영자: 레지스터 표 저장", s == 200 and j["profile"]["name"] == "다른 기종")
        s, j = adm("/api/admin/aircon_unlink", {"panel": "p1", "index": 2})
        check("운영자: 연결 끊기 → CCM 센서도 걷음", s == 200 and not any(x["key"].startswith("ac2_") for x in MS.specs_of(st, "ccm-1")))
        sp = E.get(st, "p1")
        sp["equipment"] = sp["equipment"][1:]        # 첫 에어컨(연결됨)을 화면에서 지우고 저장
        s, _ = adm("/api/admin/panel_spec", {"panel": "p1", "spec": sp})
        check("장치를 지우고 저장하면 그 연결 센서도 CCM에서 걷음", s == 200 and not MS.specs_of(st, "ccm-1"), str([x["key"] for x in MS.specs_of(st, "ccm-1")]))
        lk = AC.link(st, "p1", 1, "ccm-1", "10.0.0.5", 502, 1, "다른 기종", "t")
        check("새 에어컨은 값 기록이 남은 번호(ac1)를 피함", lk["tag"] != "ac1", lk["tag"])
        lk2 = AC.link(st, "p1", 1, "ccm-1", "10.0.0.6", 502, 1, "다른 기종", "t")
        check("같은 에어컨을 IP만 바꿔 다시 연결 → 번호 그대로(값이 들어온 뒤에도)", lk2["tag"] == lk["tag"], lk2["tag"])
        cu = client()
        cu("/api/login", {"user": "view.a", "password": tpw})
        cu("/api/me/password", {"old": tpw, "new": "view-a-pw-2026"})
        cu("/api/login", {"user": "view.a", "password": "view-a-pw-2026"})
        s, j = cu("/api/guard/panel?panel=p1")
        u = next(x for x in j["equipment"]["units"] if x.get("live"))
        check("고객: 연결된 에어컨의 지금 상태", s == 200 and "state" in u["live"])
        check("고객 화면엔 IP·레지스터 같은 것이 없음", "10.0.0.5" not in json.dumps(j) and "register" not in json.dumps(j))
        check("고객은 연결을 못 바꿈", cu("/api/admin/aircon_link", {"panel": "p1", "index": 1})[0] == 403)
    finally:
        srv.shutdown()
        st.close()
        os.unlink(db)
    print("\n" + ("✅ 전체 통과" if not _fails else f"❌ 실패 {len(_fails)}건: {_fails}"))
    return 0 if not _fails else 1


if __name__ == "__main__":
    sys.exit(run())
