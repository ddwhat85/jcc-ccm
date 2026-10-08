"""원격 시스템 명령 — 서버 응답에서 재시작·채널 재초기화만 받아들이고, 프로그램 재시작은 종료 신호로,
채널 재초기화는 드라이버를 새로 만들고 다음 보고에 '했다'를 싣는다. 출력 조작 명령은 그대로 엣지로.

python tests/test_system_cmd.py   (firmware/ 에서)
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from jcc_ccm.transport import parse_commands  # noqa: E402


class _T:
    def __init__(self, cmds):
        self.cmds = cmds

    def take_commands(self):
        c, self.cmds = self.cmds, []
        return c


class _Edge:
    def __init__(self):
        self.got = []

    def apply_command(self, cmd):
        self.got.append(cmd)


class _Cfg:
    sensors = []
    modbus = None


def _agent(cmds, edge=None):
    from jcc_ccm.agent import Agent
    a = Agent.__new__(Agent)
    a._transport = _T(cmds)
    a._edge = edge
    a._manual = None
    a._cfg = _Cfg()
    a._drivers = ["old"]
    a._restart = a._stop = False
    a._cmd_done = []
    a._started = 1000.0
    return a


def test_parse_keeps_known_system_commands_only():
    got = parse_commands({"commands": [{"system": "restart_agent"}, {"system": "rm -rf"}, {"system": "restart_channel", "sensor_key": "h2_lel"},
                                       {"actuator": "vent", "action": "open"}, {"bad": 1}]})
    assert got == [{"system": "restart_agent", "sensor_key": ""}, {"system": "restart_channel", "sensor_key": "h2_lel"},
                   {"actuator": "vent", "action": "open"}], got


def test_restart_agent_stops_for_systemd_restart():
    a = _agent([{"system": "restart_agent", "sensor_key": ""}])
    a._apply_commands()
    assert a._stop and a._restart


def test_restart_channel_rebuilds_drivers_and_reports():
    import jcc_ccm.agent as ag
    built = []
    orig = ag.build_drivers
    ag.build_drivers = lambda sensors, modbus: built.append(1) or ["new"]
    try:
        a = _agent([{"system": "restart_channel", "sensor_key": "h2_lel"}])
        a._apply_commands()
    finally:
        ag.build_drivers = orig
    assert built and a._drivers == ["new"] and not a._stop
    done = a._take_done()
    assert done and done[0]["system"] == "restart_channel" and done[0]["sensor_key"] == "h2_lel"
    assert a._take_done() == []                      # 한 번 실으면 비운다


def test_actuator_commands_still_go_to_edge_and_work_without_edge():
    e = _Edge()
    a = _agent([{"actuator": "vent", "action": "open"}], e)
    a._apply_commands()
    assert e.got == [{"actuator": "vent", "action": "open"}]
    b = _agent([{"system": "restart_agent", "sensor_key": ""}], None)   # 출력 장치가 없는 CCM도 재시작은 받는다
    b._apply_commands()
    assert b._stop


if __name__ == "__main__":
    n = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            n += 1
            print("PASS", name)
    print(f"✅ {n}개 통과")
