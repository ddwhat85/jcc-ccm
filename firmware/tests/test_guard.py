"""출력 작동 확인(ActuatorGuard) 테스트 — 가상 릴레이에 고장을 주입한다.

    cd firmware && python tests/test_guard.py
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

from jcc_ccm import config as cfgmod
from jcc_ccm.actuators import LogActuator
from jcc_ccm.config import ActuatorConfig
from jcc_ccm.guard import ActuatorGuard
from test_edge import Rig, _BASE, _PREDICT, _load, _noisy


class Clock:
    def __init__(self, t=1_790_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


def _vent(feedback="switch", travel=30.0, retries=2, check_every=30.0, **sim):
    clk = Clock()
    a = ActuatorConfig(kind="vent", driver="log", feedback=feedback, travel_seconds=travel,
                       retries=retries, check_every=check_every)
    a.validate()
    act = LogActuator(a)
    act.clock = clk
    for k, v in sim.items():
        setattr(act, k, v)
    return act, ActuatorGuard(act, a, clk), clk


def _cmd(act, g, clk, on):
    assert act.set(on)
    g.commanded(on, clk.t)


def _run(g, clk, secs, step=2.0):
    evs = []
    for _ in range(int(secs / step)):
        clk.t += step
        evs += g.tick(clk.t)
    return evs


def test_confirms_when_position_follows():
    act, g, clk = _vent(sim_delay=12.0)
    _cmd(act, g, clk, True)
    assert g.status == "moving"
    _run(g, clk, 10)
    assert g.status == "moving", "작동 시간 안엔 기다린다"
    _run(g, clk, 4)
    assert g.status == "ok" and g.position == "open", (g.status, g.position)


def test_slow_actuator_within_travel_is_ok():
    act, g, clk = _vent(sim_delay=28.0)
    _cmd(act, g, clk, True)
    evs = _run(g, clk, 34)
    assert g.status == "ok" and not [e for e in evs if e["level"] == "crit"], evs


def test_stuck_vent_retries_then_faults():
    act, g, clk = _vent(sim_stuck=False)          # 댐퍼가 닫힌 채 걸림
    _cmd(act, g, clk, True)
    evs = _run(g, clk, 32)
    assert g.status == "moving" and act.writes.count(True) == 2, ("1차 재시도", act.writes)
    evs += _run(g, clk, 70)
    assert g.status == "fault", g.status
    assert act.writes.count(True) == 3, act.writes          # 최초 1 + 재시도 2
    assert "명령 개방" in g.fault and "실제 닫힘" in g.fault, g.fault
    assert sum(1 for e in evs if e["kind"] == "fault") == 1, evs


def test_fault_rewrites_rarely_and_recovers_when_fixed():
    act, g, clk = _vent(sim_stuck=False)
    _cmd(act, g, clk, True)
    _run(g, clk, 100)
    assert g.status == "fault"
    n = len(act.writes)
    _run(g, clk, 60)
    assert len(act.writes) - n <= 2, "고장 중엔 check_every마다 한 번만 다시 써 본다(구동기 혹사 금지)"
    act.sim_stuck = None                          # 현장에서 고침
    evs = _run(g, clk, 40)
    assert g.status == "ok", g.status
    assert any(e["kind"] == "recovered" for e in evs), evs


def test_coil_mode_detects_module_reboot_and_rewrites():
    act, g, clk = _vent(feedback="coil")
    _cmd(act, g, clk, True)
    _run(g, clk, 4)
    assert g.status == "ok"
    act.sim_coil_reset()                          # 릴레이 모듈 재부팅 → 코일 풀림
    evs = _run(g, clk, 32)
    assert any(e["kind"] == "restored" for e in evs), evs
    assert act.read_state() is True and g.status in ("ok", "moving")


def test_manual_move_without_command_is_reported():
    act, g, clk = _vent()
    _cmd(act, g, clk, True)
    _run(g, clk, 4)
    assert g.status == "ok"
    act.sim_stuck = False                         # 누가 손으로 닫음(위치만 바뀜)
    evs = _run(g, clk, 32)
    assert any(e["kind"] == "moved" and e["level"] == "warn" for e in evs), evs


def test_read_failure_is_unknown_not_fault():
    act, g, clk = _vent(sim_read_fail=True)
    _cmd(act, g, clk, True)
    evs = _run(g, clk, 120)
    assert g.status == "unknown", g.status
    assert not [e for e in evs if e["kind"] == "fault"], evs
    act.sim_read_fail = False
    _run(g, clk, 4)
    assert g.status == "ok"


def test_feedback_none_is_off():
    act, g, clk = _vent(feedback="none")
    _cmd(act, g, clk, True)
    assert _run(g, clk, 60) == [] and g.status == "off"


def test_config_validation():
    for kw, needle in (({"feedback": "bogus"}, "feedback"),
                       ({"feedback": "switch", "driver": "modbus_coil", "modbus_slave": 3}, "feedback_input"),
                       ({"travel_seconds": 900}, "travel"),
                       ({"retries": 9}, "retries"),
                       ({"check_every": 1}, "check_every")):
        a = ActuatorConfig(kind="vent", **dict({"driver": "log"}, **kw))
        try:
            a.validate()
        except cfgmod.ConfigError as exc:
            assert needle in str(exc), (kw, exc)
            continue
        raise AssertionError(f"통과하면 안 됨: {kw}")
    a = ActuatorConfig(kind="vent", driver="modbus_coil", modbus_slave=3)
    a.validate()
    assert a.feedback_mode() == "coil" and a.travel() == 30.0
    h = ActuatorConfig(kind="heater", driver="log")
    h.validate()
    assert h.feedback_mode() == "none" and h.travel() == 3.0


def test_edge_report_carries_confirmation():
    text = _BASE + _PREDICT.replace('kind = "vent"\ndriver = "log"',
                                    'kind = "vent"\ndriver = "log"\nfeedback = "switch"\ntravel_seconds = 10')
    from jcc_ccm.edge import build_edge
    edge = build_edge(_load(text), force_log=True)
    rig = Rig(edge)
    assert rig.cycle(_noisy(0))["actuators"]["vent"]["confirm"] == "unknown"   # 아직 명령 전
    rep = rig.run(15, _noisy(0))                                               # 워밍업 뒤 첫 동기화(닫힘)
    v = rep["actuators"]["vent"]
    assert v["confirm"] == "ok" and v["position"] == "closed", v
    edge._acts["vent"].sim_stuck = False
    edge._acts["vent"].clock = lambda: rig.t
    edge.apply_command({"actuator": "vent", "action": "open"}, now=rig.t)
    reps = [rig.cycle(_noisy(i)) for i in range(40)]            # 80초
    v = reps[-1]["actuators"]["vent"]
    assert v["confirm"] == "fault" and "실제 닫힘" in v["fault"], v
    evs = [a for r in reps for a in r["actions"] if a.get("event")]
    assert any(a["event"] == "fault" and a["level"] == "crit" for a in evs), evs


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = 0
    for fn in fns:
        try:
            fn(); passed += 1; print(f"  PASS {fn.__name__}")
        except Exception as exc:  # noqa: BLE001
            import traceback
            print(f"  FAIL {fn.__name__}: {exc}")
            traceback.print_exc()
    print(f"\n{passed}/{len(fns)} passed")
    sys.exit(0 if passed == len(fns) else 1)
