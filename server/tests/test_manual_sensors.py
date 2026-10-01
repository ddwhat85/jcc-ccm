"""노드 추가 — 센서 직접 지정: 검사·저장·인벤토리·CCM 왕복(실제 펌웨어 코드)·삭제·재탐색 유지·운영 탐색.

python -m tests.test_manual_sensors   (server/ 에서)
"""
from __future__ import annotations

import http.cookiejar
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from types import SimpleNamespace as NS

os.environ["JCC_DASHBOARD_PW"] = "env-admin-pw"      # app import 전에
os.environ["JCC_DASHBOARD_USER"] = "jccops"
os.environ.pop("JCC_API_KEY", None)
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "firmware"))

from jcc_server import app as appmod
from jcc_server.storage import Storage
from jcc_ccm.manual import ManualSensors
from jcc_ccm.transport.http_transport import HttpTransport

_fails = []


def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not cond:
        _fails.append(name)


RTU = {"key": "h2_manual", "name": "수소(수동)", "unit": "%LEL", "driver": "modbus", "slave": 9,
       "register": 0, "type": "input", "datatype": "uint16", "scale": 0.1, "offset": 0}
TCP = {"key": "meter_kw", "name": "전력계", "unit": "kW", "driver": "modbus_tcp", "slave": 1, "register": 3000,
       "type": "holding", "datatype": "float32", "scale": 1, "offset": 0, "host": "192.0.2.10", "port": 502}


def run():
    fd, db = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    st = Storage(db)
    now = time.time()
    st.set_discovery({"panel": "p1", "panel_name": "1번 판넬", "ccms": [{"device_id": "ccm-1", "sensors": [
        {"key": "h2_lel", "name": "수소", "unit": "%LEL", "kind": "h2", "confidence": "식별", "address": 1},
        {"key": "unknown_9", "name": "미확인", "unit": "?", "kind": "analog", "confidence": "추정", "address": 9}]}]})
    st.ingest({"device_id": "ccm-1", "panel": "p1", "readings": [
        {"key": "h2_lel", "name": "수소", "unit": "%LEL", "value": 0.4, "ok": True, "ts": now}]})
    srv = appmod.make_server("127.0.0.1", 0, st)
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def call(path, body=None):
        req = urllib.request.Request(base + path, method="POST" if body is not None else "GET",
                                     data=None if body is None else json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        try:
            with op.open(req, timeout=10) as r:
                return r.status, json.loads(r.read().decode("utf-8") or "{}")
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode("utf-8") or "{}")

    tmp = tempfile.mkdtemp()
    try:
        print("=== 권한·입력 검사 ===")
        check("새 경로가 관리자 전용", all(appmod.route_policy(m, p) == "admin" for m, p in
                                     (("GET", "/api/sensor/manual"), ("POST", "/api/sensor/manual"),
                                      ("GET", "/api/sensor/profiles"))))
        check("로그인 전 401", call("/api/sensor/manual", {"action": "add"})[0] == 401)
        call("/api/login", {"user": "jccops", "password": "env-admin-pw"})
        s, j = call("/api/sensor/profiles")
        idents = [p["ident"] for p in j["profiles"]]
        check("제품 목록(CCM 내장 제외)", s == 200 and "INFRASENSING-H2" in idents and not any(i.startswith("TURCK-CCM") for i in idents),
              str(idents[:4]))
        for bad, why in ((dict(RTU, slave=0), "Modbus 주소"), (dict(TCP, host="a b"), "IP"),
                         (dict(RTU, key="h2_lel"), "이미"), (dict(RTU, datatype="x"), "형식")):
            s, j = call("/api/sensor/manual", {"action": "add", "device_id": "ccm-1", "spec": bad})
            check(f"거부: {why}", s == 400 and why in j.get("error", ""), j.get("error", ""))
        s, j = call("/api/sensor/manual", {"action": "add", "device_id": "ccm-없음", "spec": RTU})
        check("없는 CCM 거부", s == 400 and "없는 CCM" in j["error"])

        print("\n=== 추가 → 화면 인벤토리 ===")
        s, j = call("/api/sensor/manual", {"action": "add", "device_id": "ccm-1", "spec": RTU,
                                           "meta": {"kind": "h2", "brand": "InfraSensing", "alarm_warn": 10, "alarm_max": 25}})
        check("RTU 추가 v1", s == 200 and j["sensor"]["version"] == 1, str(j))
        s, j = call("/api/sensor/manual", {"action": "add", "device_id": "ccm-1", "spec": TCP})
        check("TCP 추가 v2", s == 200 and j["sensor"]["version"] == 2)
        s, j = call("/api/sensor/manual", {"action": "add", "device_id": "ccm-1", "spec": RTU})
        check("같은 키 두 번 거부", s == 400)
        d = next(x for x in call("/api/devices")[1]["devices"] if x["device_id"] == "ccm-1")
        man = {x["sensor_key"]: x for x in d["latest"] if x.get("confidence") == "수동"}
        check("노드로 바로 보임(수동·경보값·값 대기)", set(man) == {"h2_manual", "meter_kw"}
              and man["h2_manual"]["alarm_warn"] == 10 and man["h2_manual"]["value"] is None
              and man["meter_kw"]["address"] == "192.0.2.10:502#1", str(man.get("meter_kw", {}).get("address")))
        s, j = call("/api/sensor/manual?device_id=ccm-1")
        check("상태: CCM 반영 대기", [x["status"] for x in j["sensors"]] == ["pending", "pending"])
        check("같은 주소(9)의 '추정' 항목은 직접 지정이 대체", "unknown_9" not in {x["sensor_key"] for x in d["latest"]})

        print("\n=== CCM 왕복(실제 펌웨어 코드: HttpTransport + ManualSensors) ===")
        t = HttpTransport(NS(http=NS(url=base + "/v1/telemetry", api_key="", timeout_seconds=5)))
        m = ManualSensors(os.path.join(tmp, "manual_sensors.json"), {"h2_lel"})
        def report(readings=()):
            return t.send({"device_id": "ccm-1", "panel": "p1", "ts": time.time(), "readings": list(readings),
                           "sensor_config": m.report()})
        check("보고 전송", report())
        msg = t.take_sensor_config()
        check("응답에 수동 센서 목록(v2, 2개)", msg and msg["version"] == 2 and [x["key"] for x in msg["sensors"]]
              == ["h2_manual", "meter_kw"], str(msg)[:120])
        check("CCM 적용", m.apply(msg) and m.report()["active"] == ["h2_manual", "meter_kw"])
        report([{"key": "h2_manual", "name": "수소(수동)", "unit": "%LEL", "value": 0.7, "ok": True, "ts": time.time()}])
        check("같은 버전이면 다시 안 보냄", t.take_sensor_config() is None)
        s, j = call("/api/sensor/manual?device_id=ccm-1")
        check("상태: 적용됨", [x["status"] for x in j["sensors"]] == ["applied", "applied"], str(j["sensors"]))
        d = next(x for x in call("/api/devices")[1]["devices"] if x["device_id"] == "ccm-1")
        check("값이 노드에 들어옴", next(x for x in d["latest"] if x["sensor_key"] == "h2_manual")["value"] == 0.7)
        ev = [e["detail"] for e in st.list_events(limit=50) if e["etype"] == "manual_sensor"]
        check("기록: 지정·적용", any("직접 지정" in e for e in ev) and any("v2 적용" in e for e in ev), str(ev[:3]))

        print("\n=== 거부 보고·재탐색·삭제 ===")
        m.errors, m.status = {"meter_kw": "연결 시험 실패"}, "partial"
        m.specs = [x for x in m.specs if x["key"] != "meter_kw"]
        report()
        s, j = call("/api/sensor/manual?device_id=ccm-1")
        rej = next(x for x in j["sensors"] if x["key"] == "meter_kw")
        check("CCM 거부 이유 표시", rej["status"] == "rejected" and rej["reason"] == "연결 시험 실패")
        st.set_discovery({"panel": "p1", "ccms": [{"device_id": "ccm-1", "sensors": [
            {"key": "h2_lel", "name": "수소", "unit": "%LEL", "kind": "h2"}]}]})
        keys = {x["sensor_key"] for x in st._discovered_map()["ccm-1"]}
        check("다시 탐색해도 수동 센서 유지", keys == {"h2_lel", "h2_manual", "meter_kw"}, str(keys))
        s, j = call("/api/sensor/manual", {"action": "remove", "device_id": "ccm-1", "key": "meter_kw"})
        check("삭제", s == 200 and "meter_kw" not in {x["sensor_key"] for x in st._discovered_map()["ccm-1"]})
        report()
        msg = t.take_sensor_config()
        check("삭제도 CCM에 내려감(v3, 1개)", msg and msg["version"] == 3 and [x["key"] for x in msg["sensors"]] == ["h2_manual"])
        check("없는 것 삭제 404", call("/api/sensor/manual", {"action": "remove", "device_id": "ccm-1", "key": "zz"})[0] == 404)

        print("\n=== 운영 서버: 검색은 가상 탐색 대신 실제 보고 요약 ===")
        appmod._REQUIRE_AUTH = True
        try:
            s, j = call("/api/discover", {})
            check("운영: 가짜 장비 안 섞음(live 요약)", s == 200 and j.get("mode") == "live" and j["ccms"] == 1, str(j))
            check("운영: 인벤토리 그대로", {d["device_id"] for d in st.list_devices()} == {"ccm-1"})
        finally:
            appmod._REQUIRE_AUTH = False
    finally:
        srv.shutdown()
        st.close()
        os.unlink(db)
        shutil.rmtree(tmp)

    print()
    if _fails:
        print(f"❌ {len(_fails)} FAIL: {_fails}")
        return 1
    print("✅ 전체 통과")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
