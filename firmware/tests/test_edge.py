"""엣지 예지 테스트 — 실기·네트워크 없이 도는 것만.

    cd firmware && python tests/test_edge.py
"""
from __future__ import annotations

import collections
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(HERE)), "tools"))

from jcc_ccm import config as cfgmod
from jcc_ccm.actuators import LogActuator
from jcc_ccm.edge import EdgePredictor, build_edge
from jcc_ccm.sensors import Reading
from jcc_ccm.transport import parse_commands

_BASE = """
[device]
id = "ccm-edge"
panel = "panel-01"
[collection]
interval_seconds = 2
[transport]
kind = "http"
[transport.http]
url = "https://example/telemetry"
[[sensors]]
key = "h2_lel"
name = "수소"
driver = "modbus"
modbus_slave = 1
[[sensors]]
key = "voc_ppm"
name = "VOC"
driver = "modbus"
modbus_slave = 7
[[sensors]]
key = "cabinet_temp"
name = "함내 온도"
driver = "ambient"
metric = "temp"
[[sensors]]
key = "cabinet_humidity"
name = "함내 습도"
driver = "ambient"
metric = "hum"
[[sensors]]
key = "main_current"
name = "전류"
driver = "modbus"
modbus_slave = 2
[[sensors]]
key = "ncontact_temp"
name = "접점온도"
driver = "modbus"
modbus_slave = 6
"""

_PREDICT = """
[predict]
enabled = true
failsafe_after_seconds = 60
state_file = ""
[predict.roles]
h2 = "h2_lel"
voc = "voc_ppm"
ambient = "cabinet_temp"
humidity = "cabinet_humidity"
current = "main_current"
contact_temp = "ncontact_temp"
[[actuators]]
kind = "vent"
driver = "log"
[[actuators]]
kind = "heater"
driver = "log"
[[actuators]]
kind = "fan"
driver = "log"
"""

NORMAL = {"h2_lel": 0.4, "voc_ppm": 30, "cabinet_temp": 27, "cabinet_humidity": 45,
          "main_current": 15, "ncontact_temp": 27}


def _load(text: str):
    fd, path = tempfile.mkstemp(suffix=".toml")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)
    try:
        return cfgmod.load(path)
    finally:
        os.unlink(path)


def _edge():
    cfg = _load(_BASE + _PREDICT)
    return build_edge(cfg, force_log=True)


class Rig:
    """가상 시계로 2초마다 센서값을 넣고 한 주기를 돌린다."""

    def __init__(self, edge: EdgePredictor, t0: float = 1_790_000_000.0):
        self.edge, self.t = edge, t0
        self.reports = []

    def cycle(self, over=None, skip=()):
        self.t += 2.0
        vals = dict(NORMAL, **(over or {}))
        rs = []
        for k, v in vals.items():
            if k in skip:
                continue
            ok = v is not None
            rs.append(Reading(key=k, name=k, unit="", value=v if ok else None, ok=ok, timestamp=self.t))
        self.edge.observe(rs)
        rep = self.edge.step(self.t + 0.5)
        self.reports.append(rep)
        return rep

    def run(self, n, over=None, skip=()):
        for _ in range(n):
            rep = self.cycle(over, skip)
        return rep

    def acts(self):
        return self.edge._acts


def _noisy(i):   # 실센서 같은 작은 잡음(완전 평탄하면 z가 정의되지 않음)
    return {"h2_lel": 0.4 + 0.05 * ((i * 7) % 5 - 2), "voc_ppm": 30 + ((i * 3) % 5 - 2)}


# ── 코어 동일성 ────────────────────────────────────────────
def test_edge_core_matches_server():
    import sync_edge_core
    assert sync_edge_core.differing() == [], sync_edge_core.differing()


# ── 설정 ────────────────────────────────────────────────────
def test_config_predict_parsed():
    cfg = _load(_BASE + _PREDICT)
    assert cfg.predict.enabled and cfg.predict.roles["h2"] == "h2_lel"
    assert [a.kind for a in cfg.actuators] == ["vent", "heater", "fan"]
    assert cfg.actuators[0].failsafe == "open"          # 벤트 기본 fail-safe = 개방


def test_config_rejects_bad_predict():
    bad = [
        _PREDICT.replace('h2 = "h2_lel"', 'oxygen = "h2_lel"'),                 # 없는 역할
        _PREDICT.replace('h2 = "h2_lel"', 'h2 = "no_such_sensor"'),             # 없는 센서
        _PREDICT.replace('kind = "vent"\ndriver = "log"', 'kind = "vent"\ndriver = "modbus_coil"'),  # 슬레이브 없음
        _PREDICT.replace("enabled = true", "enabled = false"),                  # 출력 있는데 예지 꺼짐
        _PREDICT + '[[actuators]]\nkind = "vent"\ndriver = "log"\n',            # 벤트 중복
    ]
    for i, p in enumerate(bad):
        try:
            _load(_BASE + p)
        except cfgmod.ConfigError:
            continue
        raise AssertionError(f"잘못된 설정 #{i}이 통과됨")


def test_disabled_predict_does_not_block_boot():
    # 배선 전 예시처럼 predict가 꺼져 있으면 아직 없는 센서를 가리키는 역할이 있어도 부팅된다
    cfg = _load(_BASE + '[predict]\nenabled = false\n[predict.roles]\nh2 = "not_wired_yet"\n')
    assert cfg.predict.enabled is False


def test_predict_disabled_means_no_edge():
    assert build_edge(_load(_BASE)) is None


# ── 출력 원칙 ────────────────────────────────────────────────
def test_warmup_does_not_touch_outputs():
    rig = Rig(_edge())
    rep = rig.run(3)
    assert all(a.writes == [] for a in rig.acts().values()), "워밍업 중에 출력을 썼다"
    assert rep["fire"]["stage"] == "pending"


def test_ready_syncs_outputs_once_then_quiet():
    rig = Rig(_edge())
    for i in range(20):
        rig.cycle(_noisy(i))
    vent = rig.acts()["vent"]
    assert vent.writes == [False], vent.writes          # 첫 판정 때 한 번 '닫힘' 동기화
    for i in range(10):
        rig.cycle(_noisy(i))
    assert vent.writes == [False], "상태가 같은데 또 썼다"


def test_fire_precursor_opens_vent_autonomously():
    rig = Rig(_edge())
    for i in range(20):
        rig.cycle(_noisy(i))
    opened = None
    for i in range(15):   # H2·VOC 동반 상승(임계 25%LEL·1000ppm 아래)
        rep = rig.cycle({"h2_lel": 1 + i * 1.0, "voc_ppm": 30 + i * 40})
        if opened is None and rep["actuators"]["vent"]["on"]:
            opened = rep
    assert opened is not None, "화재 징조에도 벤트가 안 열렸다"
    act = [a for a in opened["actions"] if a["actuator"] == "vent"][-1]
    assert act["on"] is True and "화재 징조" in act["why"], act
    assert opened["fire"]["stage"] in ("danger", "critical")


def test_vent_closes_after_sustained_recovery():
    rig = Rig(_edge())
    for i in range(20):
        rig.cycle(_noisy(i))
    for i in range(15):
        rig.cycle({"h2_lel": 1 + i * 1.0, "voc_ppm": 30 + i * 40})
    assert rig.acts()["vent"].state is True
    for i in range(60):   # 정상 2분 → FRI<20 유지 60초 뒤 닫힘
        rig.cycle(_noisy(i))
    assert rig.acts()["vent"].state is False, rig.acts()["vent"].writes


def test_gas_sensor_loss_triggers_failsafe_open():
    rig = Rig(_edge())
    for i in range(20):
        rig.cycle(_noisy(i))
    assert rig.acts()["vent"].state is False
    rep = None
    for _ in range(30):   # 가스 센서 2개가 60초 넘게 무소식
        rep = rig.cycle(skip=("h2_lel", "voc_ppm"))
    assert rig.acts()["vent"].state is True, "가스 센서를 잃었는데 fail-safe 개방 안 됨"
    assert rep["failsafe"] is True and rep["actuators"]["vent"]["mode"] == "failsafe"
    why = [a["why"] for r in rig.reports for a in r["actions"] if a["actuator"] == "vent" and a["on"]]
    assert why and "fail-safe" in why[-1], why
    for i in range(60):   # 센서 복귀·정상 유지 → 히스테리시스로 닫힘
        rig.cycle(_noisy(i))
    assert rig.acts()["vent"].state is False


def test_short_gas_gap_does_not_trip_failsafe():
    rig = Rig(_edge())
    for i in range(20):
        rig.cycle(_noisy(i))
    rig.run(10, skip=("h2_lel", "voc_ppm"))     # 20초 끊김(< 60초)
    assert rig.acts()["vent"].state is False
    assert rig.reports[-1]["failsafe"] is False


def test_manual_command_overrides_and_returns_to_auto():
    rig = Rig(_edge())
    for i in range(20):
        rig.cycle(_noisy(i))
    assert rig.edge.apply_command({"actuator": "vent", "action": "open"}, now=rig.t + 1)
    assert rig.acts()["vent"].state is True
    rep = rig.cycle(_noisy(0))
    assert rep["actuators"]["vent"] == {"on": True, "mode": "manual"}   # 정상이어도 사람 우선
    rig.edge.apply_command({"actuator": "vent", "action": "auto"}, now=rig.t + 1)
    for i in range(40):
        rig.cycle(_noisy(i))
    assert rig.acts()["vent"].state is False                            # 자동 복귀 후 닫힘


def test_bad_commands_are_ignored():
    rig = Rig(_edge())
    rig.run(20)
    assert not rig.edge.apply_command({"actuator": "door", "action": "open"})
    assert not rig.edge.apply_command({"actuator": "vent", "action": "explode"})


def test_failed_write_is_retried():
    rig = Rig(_edge())
    vent = rig.acts()["vent"]
    fails = {"n": 1}
    real = vent.set

    def flaky(on):
        if fails["n"]:
            fails["n"] -= 1
            vent.writes.append(("fail", on))
            return False
        return real(on)
    vent.set = flaky
    for i in range(21):
        rig.cycle(_noisy(i))
    assert vent.state is False and vent.writes[0] == ("fail", False), vent.writes
    fail_acts = [a for r in rig.reports for a in r["actions"] if a["actuator"] == "vent" and not a["ok"]]
    assert fail_acts, "쓰기 실패가 보고되지 않았다"


def test_condensation_drives_heater_and_fan():
    rig = Rig(_edge())
    for i in range(20):
        rig.cycle(_noisy(i))
    rig.run(8, {"cabinet_humidity": 96})
    assert rig.acts()["heater"].state is True and rig.acts()["fan"].state is True


# ── 접점 발열 기준: 재부팅 유지·재학습 명령 ──────────────────
def _quick_edge(state_file: str):
    from jcc_ccm.predict.contact_heat import ContactCfg
    cfg = _load(_BASE + _PREDICT.replace('state_file = ""', f'state_file = "{state_file.replace(chr(92), "/")}"'))
    edge = build_edge(cfg, force_log=True)
    edge._pred.contact_cfg = ContactCfg(learn_samples=8, learn_span=10)   # 시험용 빠른 학습
    edge._pred._panels.clear()
    edge._load_state()
    return edge


def test_contact_baseline_survives_reboot():
    fd, path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    os.unlink(path)
    try:
        rig = Rig(_quick_edge(path))
        for i in range(20):
            rep = rig.cycle(_noisy(i))
        assert rep["contact"]["baseline"]["status"] == "ready", rep["contact"]
        k1 = rig.edge._pred.contact_baseline("panel-01").k
        assert os.path.isfile(path), "기준 확정 때 즉시 저장되지 않았다"
        edge2 = _quick_edge(path)                           # 재부팅
        bl2 = edge2._pred.contact_baseline("panel-01")
        assert bl2.ready and bl2.k == k1, (bl2.status, bl2.k, k1)
    finally:
        for p in (path, path + ".tmp"):
            if os.path.exists(p):
                os.unlink(p)


def test_relearn_command_resets_contact_baseline():
    fd, path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    os.unlink(path)
    try:
        rig = Rig(_quick_edge(path))
        for i in range(20):
            rig.cycle(_noisy(i))
        assert rig.edge.apply_command({"actuator": "contact", "action": "relearn"})
        rep = rig.cycle(_noisy(0))
        assert rep["contact"]["baseline"]["status"] == "learning", rep["contact"]
        import json as _j
        with open(path, encoding="utf-8") as fh:
            assert _j.load(fh)["contact_baseline"]["status"] == "learning"   # 재부팅해도 재학습 유지
    finally:
        for p in (path, path + ".tmp"):
            if os.path.exists(p):
                os.unlink(p)


def test_unwritable_state_file_does_not_break_loop():
    rig = Rig(_quick_edge(os.path.join(tempfile.gettempdir(), "no_such_dir_jcc", "s.json")))
    for i in range(20):
        rep = rig.cycle(_noisy(i))
    assert rep["contact"]["baseline"]["status"] == "ready"      # 저장 실패해도 판정은 계속


# ── 에이전트·전송 연결 ───────────────────────────────────────
def test_parse_commands_is_strict():
    assert parse_commands({"commands": [{"actuator": "vent", "action": "open"}, {"x": 1}, "junk"]}) \
        == [{"actuator": "vent", "action": "open"}]
    assert parse_commands({"actuator": "fan", "action": "on"}) == [{"actuator": "fan", "action": "on"}]
    assert parse_commands({"ok": True}) == [] and parse_commands(None) == []


def test_agent_reports_edge_and_applies_server_commands():
    from jcc_ccm.agent import Agent
    cfg = _load(_BASE + _PREDICT)

    class FakeDriver:
        def __init__(self, key, val):
            self.key, self.val = key, val

        def read(self):
            return Reading(key=self.key, name=self.key, unit="", value=self.val, ok=True)

    class FakeTransport:
        def __init__(self):
            self.sent, self.cmds = [], []

        def send(self, p):
            self.sent.append(p)
            return True

        def take_commands(self):
            out, self.cmds = self.cmds, []
            return out

    a = Agent.__new__(Agent)
    a._cfg = cfg
    a._drivers = [FakeDriver(k, v) for k, v in NORMAL.items()]
    a._transport = FakeTransport()
    a._queue = collections.deque(maxlen=100)
    a._stop = False
    a._edge = build_edge(cfg, force_log=True)
    a._tick()
    assert "edge" in a._transport.sent[-1] and a._transport.sent[-1]["edge"]["panel"] == "panel-01"
    a._transport.cmds = [{"actuator": "fan", "action": "on"}]
    a._tick()                                   # 이번 전송 응답으로 받은 명령 → 적용
    assert a._edge._acts["fan"].state is True
    a._tick()
    acts = a._transport.sent[-1]["edge"]["actions"] + a._transport.sent[-2]["edge"]["actions"]
    assert any(x["actuator"] == "fan" and x["on"] and x["mode"] == "manual" for x in acts), acts


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
