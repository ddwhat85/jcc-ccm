"""설치 점검(시운전) 종단 — 실제 HTTP 서버 + 가상 CCM 에이전트(벤트 위치 스위치 있음).

연결·센서·예지 역할·출력 시험(벤트 합격 → 걸림 주입 시 불합격)·완료 기록·고객 열람 범위.
python -m tests.test_commission   (server/ 에서)
"""
from __future__ import annotations

import collections
import http.cookiejar
import json
import os
import sys
import tempfile
import threading
import urllib.error
import urllib.request

os.environ["JCC_DASHBOARD_PW"] = "cm-admin-pw"
os.environ["JCC_DASHBOARD_USER"] = "installer"
os.environ.pop("JCC_API_KEY", None)
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))

import test_edge_link as L                 # 가상 CCM 설정·드라이버·시계를 같이 쓴다
from jcc_server import commission as C
from jcc_server.app import make_server
from jcc_server.predict import Predictor as ServerPredictor
from jcc_server.storage import Storage
from jcc_ccm.actuators import build_actuators
from jcc_ccm.agent import Agent
from jcc_ccm.edge import EdgePredictor
from jcc_ccm.transport.http_transport import HttpTransport

_fails = []


def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not cond:
        _fails.append(name)


def run():
    print("=== 판정 규칙(단위) ===")
    diag = {"checks": [{"name": "데이터 수신", "status": "fail", "detail": "수신 이력 없음"},
                       {"name": "이상탐지", "status": "warn", "detail": "평소 대비"}]}
    it = C._from_diag(diag, "수소")
    check("실패 항목에 할 일", it["status"] == "fail" and "RS485" in it["advice"], str(it))
    it = C._from_diag({"checks": [{"name": "이상탐지", "status": "warn", "detail": "x"},
                                  {"name": "값 유효성", "status": "pass", "detail": "정상 측정"}]}, "수소")
    check("설치 때 무의미한 '이상탐지'는 판정에서 뺌", it["status"] == "pass", str(it))
    check("판정 우선순위 fail>wait>warn>pass", C._worst([{"status": "warn"}, {"status": "wait"}]) == "wait"
          and C._worst([{"status": "wait"}, {"status": "fail"}]) == "fail" and C._worst([]) == "pass")

    fd, db = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    st = Storage(db)
    st.predictor = ServerPredictor()
    st.set_discovery({"panel": "panel-01", "panel_name": "스마트 판넬", "site": "",
                      "ccms": [{"device_id": "ccm-edge", "sensors": [
                          {"key": k, "name": k, "unit": u, "kind": kd} for k, kd, u in L.SENSORS]}]})
    srv = make_server("127.0.0.1", 0, st)
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    def client():
        jar = http.cookiejar.CookieJar()
        op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))

        def call(path, body=None):
            req = urllib.request.Request(base + path, method="POST" if body is not None else "GET",
                                         data=None if body is None else json.dumps(body).encode(),
                                         headers={"Content-Type": "application/json"})
            try:
                with op.open(req, timeout=10) as r:
                    return r.status, json.loads(r.read().decode("utf-8") or "{}")
            except urllib.error.HTTPError as e:
                return e.code, json.loads(e.read().decode("utf-8") or "{}")
        return call

    try:
        cfg = L.fw_config(base + "/v1/telemetry")
        clock, values = L.Clock(), dict(L.NORMAL)
        clock.t = __import__("time").time() - 5      # 설치 점검은 '지금' 수신 신선도를 본다
        agent = Agent.__new__(Agent)
        agent._cfg, agent._drivers = cfg, [L.FakeDriver(k, clock, values) for k, _, _ in L.SENSORS]
        agent._transport = HttpTransport(cfg)
        agent._queue, agent._stop = collections.deque(maxlen=500), False
        agent._edge = EdgePredictor(cfg, build_actuators(cfg.actuators, cfg.modbus, force_log=True), clock=clock)
        vent = agent._edge._acts["vent"]

        seq = [0]

        def tick(n=1):
            # 실센서처럼 모든 값에 작은 잡음(완전히 같은 값이 이어지면 '고착'으로 판정되는 게 맞다)
            for _ in range(n):
                seq[0] += 1
                k = seq[0]
                clock.t += 2.0
                values.update({key: v + 0.02 * v * (((k * 7 + j * 3) % 5) - 2) / 2 for j, (key, v) in enumerate(L.NORMAL.items())})
                agent._tick()

        a = client()
        s, _ = a("/api/login", {"user": "installer", "password": "cm-admin-pw"})
        check("설치 기사(관리자) 로그인", s == 200)

        print("\n=== 설치 점검 ===")
        tick(2)
        s, rep = a("/api/commission/check?panel=panel-01")
        steps = {x["key"]: x for x in rep["steps"]}
        check("점검 단계 4개", s == 200 and list(steps) == ["connect", "sensors", "roles", "outputs"], str(list(steps)))
        check("값이 적을 땐 센서 '관찰 중'", steps["sensors"]["status"] == "wait", steps["sensors"]["status"])
        tick(12)
        s, rep = a("/api/commission/check?panel=panel-01")
        steps = {x["key"]: x for x in rep["steps"]}
        check("연결: 현장 예지 켜짐", any(i["target"] == "현장 예지" and i["status"] == "pass" for i in steps["connect"]["items"]))
        check("센서: 값이 쌓이면 판정", steps["sensors"]["status"] in ("pass", "warn"),
              str([(i["target"], i["status"], i["detail"]) for i in steps["sensors"]["items"] if i["status"] != "pass"][:2]))
        roles = {i["target"]: i for i in steps["roles"]["items"]}
        check("예지 역할: 화재·접점·결로 가능", all(roles[t]["status"] == "pass" for t in ("화재 징조 예지", "접점 발열 예지", "결로 예지")))
        check("CCM 설정 역할과 서버 판단 일치", roles.get("CCM ccm-edge 역할 설정", {}).get("status") == "pass",
              str(roles.get("CCM ccm-edge 역할 설정")))
        outs = {i["target"]: i for i in steps["outputs"]["items"]}
        check("출력: 벤트·팬 '시험 전'", outs.get("벤트", {}).get("status") == "wait" and "팬" in outs, str(list(outs)))
        s, j = a("/api/commission/complete", {"panel": "panel-01", "note": "시험 전"})
        check("시험 전 완료 기록은 '미완료 항목 있음'", s == 200 and j["overall"] == "wait", str(j))

        print("\n=== 출력 시험 ===")
        s, _ = a("/api/commission/output_test", {"panel": "panel-01", "actuator": "vent"})
        check("벤트 시험 시작", s == 200)
        s, j = a("/api/commission/output_test", {"panel": "panel-01", "actuator": "vent"})
        check("시험 중 중복 시작 거부", s == 400)
        for _ in range(20):
            tick(1)
            _, rep = a("/api/commission/check?panel=panel-01")
            v = {i["target"]: i for i in {x["key"]: x for x in rep["steps"]}["outputs"]["items"]}["벤트"]
            if v["status"] != "wait":
                break
        check("벤트: 열림·닫힘 실제 확인 → 합격", v["status"] == "pass", str(v))
        tick(2)
        va = st.edge_state()["ccm-edge"]["actuators"]["vent"]
        check("시험 끝나면 자동 복귀", va["mode"] == "auto", str(va))

        s, _ = a("/api/commission/output_test", {"panel": "panel-01", "actuator": "fan"})
        for _ in range(12):
            tick(1)
            _, rep = a("/api/commission/check?panel=panel-01")
            f = {i["target"]: i for i in {x["key"]: x for x in rep["steps"]}["outputs"]["items"]}["팬"]
            if f["status"] != "wait":
                break
        check("팬(확인 장치 없음): 반영만 확인 → 주의", f["status"] == "warn" and "릴레이만" in f["detail"], str(f))

        vent.sim_stuck = False                       # 벤트 걸림 주입 → 다시 시험
        a("/api/commission/output_test", {"panel": "panel-01", "actuator": "vent"})
        for _ in range(30):
            tick(1)
            _, rep = a("/api/commission/check?panel=panel-01")
            v = {i["target"]: i for i in {x["key"]: x for x in rep["steps"]}["outputs"]["items"]}["벤트"]
            if v["status"] != "wait":
                break
        check("걸린 벤트 → 불합격(사유·할 일)", v["status"] == "fail" and "실제 닫힘" in v["detail"] and v["advice"], str(v))
        tick(2)
        check("실패해도 자동 복귀", st.edge_state()["ccm-edge"]["actuators"]["vent"]["mode"] == "auto")
        s, j = a("/api/commission/complete", {"panel": "panel-01", "note": "벤트 구동기 교체 필요"})
        check("완료 기록 → 불합격", s == 200 and j["overall"] == "fail" and j["verdict"] == "불합격", str(j))
        vent.sim_stuck = None

        print("\n=== 기록 열람 범위 ===")
        s, j = a("/api/commission/reports")
        check("관리자는 기록 전부", s == 200 and len(j["reports"]) == 2 and j["reports"][0]["report"]["note"] == "벤트 구동기 교체 필요")
        ac = st.accounts
        ca, cb = ac.create_customer("A"), ac.create_customer("B")
        ac.assign_panel("panel-01", ca)
        _, ta = ac.create_user("viewer.a", "viewer", ca)
        _, tb = ac.create_user("viewer.b", "viewer", cb)
        for who, temp, want in (("viewer.a", ta, 2), ("viewer.b", tb, 0)):
            c = client()
            c("/api/login", {"user": who, "password": temp})
            c("/api/me/password", {"old": temp, "new": who + "-pw-2026"})
            c("/api/login", {"user": who, "password": who + "-pw-2026"})
            s, j = c("/api/commission/reports")
            check(f"{who}: 자기 판넬 기록 {want}건", s == 200 and len(j["reports"]) == want, str(len(j.get("reports", []))))
            check(f"{who}: 설치 점검 실행은 403", c("/api/commission/check?panel=panel-01")[0] == 403)
        check("없는 판넬 404", a("/api/commission/check?panel=nope")[0] == 404)
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
    import logging
    logging.basicConfig(level=logging.CRITICAL)
    raise SystemExit(run())
