"""외부 게이트웨이 HTTP 수신 — 등록·토큰·세 가지 모양·새 항목 자동 등록·경보 판정·끄기·토큰 새로.

python -m tests.test_gateway   (server/ 에서)
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
from jcc_server import gateway as GW
from jcc_server.storage import Storage

_fails = []


def check(name, cond, detail=""):
    print(("[PASS] " if cond else "[FAIL] ") + name + (f" — {detail}" if detail else ""))
    if not cond:
        _fails.append(name)


def run() -> int:
    now = time.time()
    print("=== 모양 바꾸기 ===")
    gw = {"id": 3, "name": "1층 LoRa", "panel": "p1", "panel_name": "A동", "site": ""}
    b = GW.to_batches(gw, {"devEUI": "24E1-2412", "temperature": 25.1, "humidity": 51, "battery": 98, "rssi": -80, "ok": True}, now)
    r = {x["key"]: x for x in b[0]["readings"]}
    check("평평한 값: 장치 = gw번호-EUI", b[0]["device_id"] == "gw3-24E12412", b[0]["device_id"])
    check("평평한 값: 종류·단위 짐작", r["temperature"]["kind"] == "temp" and r["humidity"]["unit"] == "%RH" and r["battery"]["kind"] == "other")
    check("rssi·불리언은 버림", "rssi" not in r and "ok" not in r)
    b = GW.to_batches(gw, [{"deviceId": "a", "co2": 800}, {"deviceId": "b", "ambient_temp": 30}], now)
    check("목록: 장치마다 묶음", sorted(x["device_id"] for x in b) == ["gw3-a", "gw3-b"] and b[1]["readings"][0]["kind"] in ("temp", "co2"))
    b = GW.to_batches(gw, {"readings": [{"key": "cab_t", "name": "함내", "unit": "C", "kind": "temp", "value": 31}, {"key": "x", "value": "abc"}]}, now)
    check("JCC 형식 그대로(숫자 아닌 값은 버림)", len(b[0]["readings"]) == 1 and b[0]["readings"][0]["name"] == "함내" and b[0]["device_id"] == "gw3")
    b = GW.to_batches(gw, {"temperature": 20, "ts": (now - 60) * 1000}, now)
    check("밀리초 시각도 받음", abs(b[0]["readings"][0]["ts"] - (now - 60)) < 1)

    fd, db = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    st = Storage(db)
    st.ingest({"device_id": "ccm-1", "panel": "p1", "panel_name": "A동", "readings": [
        {"key": "cabinet_temp", "name": "함내 온도", "unit": "C", "kind": "temp", "value": 30, "ok": True, "ts": now}]})
    srv = appmod.make_server("127.0.0.1", 0, st)
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def call(path, body=None, token=None):
        h = {"Content-Type": "application/json"}
        if token:
            h["Authorization"] = "Bearer " + token
        req = urllib.request.Request(base + path, method="POST" if body is not None else "GET",
                                     data=None if body is None else json.dumps(body).encode(), headers=h)
        try:
            with op.open(req, timeout=10) as res:
                return res.status, json.loads(res.read() or b"{}")
        except urllib.error.HTTPError as e:
            try:
                return e.code, json.loads(e.read() or b"{}")
            except ValueError:
                return e.code, {}
    try:
        print("\n=== 등록·수신 ===")
        call("/api/login", {"user": "jccops", "password": "env-admin-pw"})
        c, j = call("/api/admin/gateway", {"name": "1층 LoRa", "panel": "p1"})
        check("등록: 번호·토큰·주소(토큰은 지금만)", c == 200 and j["token"] and j["path"] == f"/v1/gateway/{j['id']}", str(j))
        gid, tok = j["id"], j["token"]
        _, lst = call("/api/admin/gateways")
        check("목록엔 토큰이 없음", lst["gateways"][0]["name"] == "1층 LoRa" and "token" not in json.dumps(lst) and "token_hash" not in json.dumps(lst))
        check("토큰 없으면 401", call(f"/v1/gateway/{gid}", {"temperature": 25})[0] == 401)
        check("틀린 토큰 401", call(f"/v1/gateway/{gid}", {"temperature": 25}, "nope")[0] == 401)
        c, j = call(f"/v1/gateway/{gid}", {"devEUI": "AA01", "temperature": 25.0, "humidity": 50}, tok)
        check("받음: 저장·새 항목 등록", c == 200 and j["stored"] == 2 and j["new_sensors"] == 2, str(j))
        p1 = next(p for p in st.list_panels() if p["panel"] == "p1")
        devs = {cc["device_id"]: cc for cc in p1["ccms"]}
        check("같은 판넬에 붙음", f"gw{gid}-AA01" in devs, str(list(devs)))
        sen = {x["sensor_key"]: x for x in devs[f"gw{gid}-AA01"]["latest"]}
        check("기본 기준이 붙음(온도 주의 38·위험 45)", sen["temperature"]["alarm_warn"] == 38 and sen["temperature"]["alarm_max"] == 45, str(sen["temperature"]))
        c, j = call(f"/v1/gateway/{gid}", {"devEUI": "AA01", "temperature": 47.0}, tok)
        check("다음 값부터 경보 판정(47℃ → 위험)", c == 200 and j["new_sensors"] == 0
              and any(a["device_id"] == f"gw{gid}-AA01" and a["severity"] == "crit" for a in st.list_active_alarms()))
        check("숫자 없으면 400", call(f"/v1/gateway/{gid}", {"devEUI": "AA01", "status": "ok"}, tok)[0] == 400)
        call("/api/admin/gateway/enable", {"id": gid, "on": False})
        check("끄면 401", call(f"/v1/gateway/{gid}", {"temperature": 25}, tok)[0] == 401)
        call("/api/admin/gateway/enable", {"id": gid, "on": True})
        c, j = call("/api/admin/gateway/rotate", {"id": gid})
        check("토큰 새로: 예전 것 못 씀, 새 것 됨", call(f"/v1/gateway/{gid}", {"temperature": 25}, tok)[0] == 401
              and call(f"/v1/gateway/{gid}", {"temperature": 25}, j["token"])[0] == 200)
        _, lst = call("/api/admin/gateways")
        check("받은 수·마지막 시각", lst["gateways"][0]["received"] >= 4 and lst["gateways"][0]["last_seen"])
        from jcc_server.heal import is_gateway_device
        check("게이트웨이 장치 구분", is_gateway_device(f"gw{gid}-AA01") and is_gateway_device("gw3") and not is_gateway_device("ccm-1"))
        with st._lock:   # 10분 조용 — CCM이면 침묵, 게이트웨이 장치는 아직 정상(기준 1시간)
            st._conn.execute("UPDATE devices SET last_seen=? WHERE device_id=?", (time.time() - 600, f"gw{gid}-AA01"))
            st._conn.commit()
        st._live_state[("dev", f"gw{gid}-AA01")] = "up"
        st.liveness_scan()
        check("게이트웨이 장치는 10분 조용해도 침묵 아님", st._live_state.get(("dev", f"gw{gid}-AA01")) == "up")
    finally:
        srv.shutdown()
        st.close()
        os.unlink(db)
    print("\n" + ("✅ 전체 통과" if not _fails else f"❌ 실패 {len(_fails)}건: {_fails}"))
    return 0 if not _fails else 1


if __name__ == "__main__":
    sys.exit(run())
