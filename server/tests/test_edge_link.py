"""엣지(CCM 펌웨어) ↔ 서버 연동 — 실제 에이전트가 실제 서버에 HTTP로 붙는다.

  1) CCM이 서버와 무관하게 화재 징조를 판정해 벤트를 직접 연다(자율)
  2) 서버는 그 보고를 받아 이벤트로 남기고 /api/predict에 '현장' 상태로 보여준다
  3) 대시보드 수동 조작 → 텔레메트리 응답으로 CCM에 내려가 실제 릴레이가 따른다
  4) 서버가 죽어 있어도 CCM의 벤트 판단은 그대로 돈다

python -m tests.test_edge_link   (server/ 에서)
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "firmware"))

from jcc_server.app import make_server
from jcc_server.predict import Predictor as ServerPredictor
from jcc_server.storage import Storage

from jcc_ccm import config as cfgmod
from jcc_ccm.agent import Agent
from jcc_ccm.edge import EdgePredictor
from jcc_ccm.actuators import build_actuators
from jcc_ccm.sensors import Reading
from jcc_ccm.transport.http_transport import HttpTransport

_fails = []


def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not cond:
        _fails.append(name)


SENSORS = [("h2_lel", "h2", "%LEL"), ("voc_ppm", "voc", "ppm"), ("cabinet_temp", "temp", "C"),
           ("cabinet_humidity", "humidity", "%RH"), ("main_current", "current", "A"),
           ("ncontact_temp", "temp", "C")]
NORMAL = {"h2_lel": 0.4, "voc_ppm": 30, "cabinet_temp": 27, "cabinet_humidity": 45,
          "main_current": 15, "ncontact_temp": 27}


def fw_config(url: str):
    text = f"""
[device]
id = "ccm-edge"
panel = "panel-01"
panel_name = "스마트 판넬"
[collection]
interval_seconds = 2
[transport]
kind = "http"
[transport.http]
url = "{url}"
timeout_seconds = 3
""" + "".join(f"""
[[sensors]]
key = "{k}"
name = "{k}"
driver = "modbus"
modbus_slave = {i + 1}
""" for i, (k, _, _) in enumerate(SENSORS)) + """
[predict]
enabled = true
[predict.roles]
h2 = "h2_lel"
voc = "voc_ppm"
ambient = "cabinet_temp"
humidity = "cabinet_humidity"
current = "main_current"
contact_temp = "ncontact_temp"
[[actuators]]
kind = "vent"
feedback = "switch"
travel_seconds = 6
[[actuators]]
kind = "fan"
"""
    fd, path = tempfile.mkstemp(suffix=".toml")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)
    try:
        return cfgmod.load(path)
    finally:
        os.unlink(path)


class Clock:
    def __init__(self):
        self.t = time.time() - 150          # 가상 시각(현재 근처 — 서버의 신선도 판단과 맞게)

    def __call__(self):
        return self.t


class FakeDriver:
    """값·시각을 테스트가 정하는 센서."""
    def __init__(self, key, clock, values):
        self.key, self.clock, self.values = key, clock, values

    def read(self):
        return Reading(key=self.key, name=self.key, unit="", value=self.values[self.key], ok=True,
                       timestamp=self.clock())


def run():
    fd, db = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    st = Storage(db)
    st.predictor = ServerPredictor()
    st.set_discovery({"panel": "panel-01", "panel_name": "스마트 판넬", "site": "",
                      "ccms": [{"device_id": "ccm-edge", "sensors": [
                          {"key": k, "name": k, "unit": u, "kind": kd} for k, kd, u in SENSORS]}]})
    srv = make_server("127.0.0.1", 0, st)
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    def api(path, body=None):
        req = urllib.request.Request(base + path, method="POST" if body is not None else "GET",
                                     data=None if body is None else json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        return json.loads(urllib.request.urlopen(req, timeout=5).read())

    try:
        cfg = fw_config(base + "/v1/telemetry")
        clock, values = Clock(), dict(NORMAL)
        agent = Agent.__new__(Agent)
        agent._cfg = cfg
        agent._drivers = [FakeDriver(k, clock, values) for k, _, _ in SENSORS]
        agent._transport = HttpTransport(cfg)
        import collections
        agent._queue = collections.deque(maxlen=500)
        agent._stop = False
        agent._edge = EdgePredictor(cfg, build_actuators(cfg.actuators, cfg.modbus, force_log=True), clock=clock)
        vent = agent._edge._acts["vent"]

        def tick(n=1, **over):
            for i in range(n):
                clock.t += 2.0
                values.update(NORMAL)
                values["h2_lel"] = 0.4 + 0.05 * ((i * 7) % 5 - 2)
                values["voc_ppm"] = 30 + ((i * 3) % 5 - 2)
                values.update({k: (v(i) if callable(v) else v) for k, v in over.items()})
                agent._tick()

        print("=== 엣지 ↔ 서버 (실제 HTTP) ===")
        tick(20)
        e = st.edge_state().get("ccm-edge")
        check("서버가 엣지 보고를 받음", e is not None and e.get("panel") == "panel-01",
              f'vent={e and e["actuators"].get("vent")}')

        tick(15, h2_lel=lambda i: 1 + i * 1.0, voc_ppm=lambda i: 30 + i * 40)
        check("CCM이 스스로 벤트 개방(자율)", vent.state is True, f"writes={vent.writes}")
        evs = [x["detail"] for x in api("/api/events?limit=50")["events"] if x["etype"] == "edge_actuate"]
        check("서버 이벤트: 현장 자율 벤트 개방", any("현장 자율: 벤트 개방" in d for d in evs), (evs or ["없음"])[0])

        st.predict_scan(now=clock.t + 1)
        pan = api("/api/predict")["panels"][0]
        ev = pan.get("edge", {}).get("ccm-edge", {})
        check("/api/predict에 현장 출력 상태", ev.get("actuators", {}).get("vent", {}).get("on") is True,
              f'edge vent={ev.get("actuators", {}).get("vent")}')

        # 대시보드 수동 조작 → 응답으로 하향 → CCM 릴레이가 따른다
        r = api("/api/predict/actuator", {"panel": "panel-01", "actuator": "vent", "action": "close"})
        check("수동 조작이 현장 CCM으로 라우팅", r.get("edge_devices") == ["ccm-edge"], str(r.get("edge_devices")))
        tick(1, h2_lel=15, voc_ppm=590)          # 이 전송의 응답에 명령이 실려 온다
        check("CCM이 명령대로 벤트 닫음(위험 중이어도 사람 우선)", vent.state is False, f"writes={vent.writes[-3:]}")
        tick(1, h2_lel=15, voc_ppm=590)
        e = st.edge_state()["ccm-edge"]
        ev_ = e["actuators"]["vent"]
        check("보고에 수동 모드 반영", (ev_["on"], ev_["mode"]) == (False, "manual"), str(ev_))
        evs = [x["detail"] for x in api("/api/events?limit=50")["events"] if x["etype"] == "edge_actuate"]
        check("서버 이벤트: 현장 수동 반영", any("현장 수동 반영: 벤트 닫힘" in d for d in evs), evs[0] if evs else "")

        # 서버에 없는 출력(히터)은 이 CCM으로 보내지 않는다
        r = api("/api/predict/actuator", {"panel": "panel-01", "actuator": "heater", "action": "on"})
        check("CCM에 없는 출력은 라우팅 안 함", r.get("edge_devices") == [], str(r.get("edge_devices")))

        # 벤트가 닫힌 채 걸림 → CCM이 재시도 후 '작동 실패' → 서버 위험 경보 → 고치면 스스로 해소
        vent.sim_stuck = False
        api("/api/predict/actuator", {"panel": "panel-01", "actuator": "vent", "action": "open"})
        tick(14)                                   # 28초 > 작동 6초 × (1 + 재시도 2)
        e = st.edge_state()["ccm-edge"]["actuators"]["vent"]
        check("CCM이 벤트 작동 실패 확정(사유)", e.get("confirm") == "fault" and "실제 닫힘" in (e.get("fault") or ""), str(e))
        al = [a for a in api("/api/alarms")["alarms"] if a["kind"] == "actuator_fault"]
        check("서버 위험 경보: 벤트 작동 실패", len(al) == 1 and al[0]["severity"] == "crit" and al[0]["sensor_key"] == "vent",
              str(al)[:160])
        vent.sim_stuck = None                      # 현장에서 고침
        tick(18)                                   # 36초 ≥ 평소 되읽기 30초
        al = [a for a in api("/api/alarms")["alarms"] if a["kind"] == "actuator_fault"]
        check("고치면 작동 실패 경보 자동 해소", not al and st.edge_state()["ccm-edge"]["actuators"]["vent"]["confirm"] == "ok",
              str(st.edge_state()["ccm-edge"]["actuators"]["vent"]))
        evs = [x["etype"] for x in api("/api/events?limit=80")["events"]]
        check("작동 실패·해소 이벤트", "actuator_fault" in evs and "actuator_fault_clear" in evs)

        # 튜닝 콘솔 적용 → 텔레메트리 응답으로 CCM에 → CCM이 한계 검증 후 적용 → 다음 보고로 서버에 기록
        from jcc_server import params as SP
        newp = dict(SP.defaults(), **{"dew.rh_high": 84.0})
        r = api("/api/tuning/apply", {"params": newp, "base_version": 0, "note": "종단 시험"})
        check("서버 적용 → v1", r.get("version") == 1, str(r)[:120])
        tick(2)                                   # 1번째 응답에 설정이 실려 오고, 2번째 보고에 결과
        tr = agent._edge.tuning_report()
        check("CCM이 받은 기준을 적용", agent._edge._pred.dew_cfg.rh_high == 84.0 and tr["version"] == 1, str(tr))
        es = api("/api/tuning/config")["edge"].get("ccm-edge", {})
        check("서버에 CCM 적용 상태(v1 ok)", es.get("last_version") == 1 and es.get("status") == "ok", str(es))
        # 서버가 뚫려 관문을 우회해 한계 밖 설정을 밀어 넣는 상황 → CCM이 거부하고 이전 기준 유지
        st.tuning_activate(dict(newp, **{"fire.open_fri": 95.0}), "침입자", "관문 우회", {}, "apply")
        tick(2)
        check("한계 밖 설정은 CCM이 거부(v1 유지)", agent._edge._pred.fire_cfg.open_fri == 55.0
              and agent._edge.tuning_report()["version"] == 1, str(agent._edge.tuning_report()))
        es = api("/api/tuning/config")["edge"].get("ccm-edge", {})
        check("서버에 CCM 거부 기록(사유)", es.get("status") == "rejected" and "한계" in es.get("error", ""), str(es))
        tick(1)
        check("거부한 버전은 다시 안 내려옴", agent._transport.take_tuning() is None)
        st.predictor.reconfigure(newp)             # 이하 시험을 위해 서버 엔진은 정상 기준으로

        # 자동 복귀 후 서버를 끊어도(회선 두절) CCM은 스스로 판정해 벤트를 연다
        api("/api/predict/actuator", {"panel": "panel-01", "actuator": "vent", "action": "auto"})
        tick(1)
        srv.shutdown()
        srv.server_close()
        tick(20)
        tick(15, h2_lel=lambda i: 1 + i * 1.0, voc_ppm=lambda i: 30 + i * 40)
        check("서버 두절 중에도 CCM이 벤트 개방", vent.state is True and len(agent._queue) > 0,
              f"대기 전송 {len(agent._queue)}건")
    finally:
        try:
            st.close()
        except Exception:
            pass
        os.unlink(db)

    print()
    if _fails:
        print(f"❌ {len(_fails)} FAIL: {_fails}")
        return 1
    print("✅ 전체 통과")
    return 0


if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.ERROR)
    raise SystemExit(run())
