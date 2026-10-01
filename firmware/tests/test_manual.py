"""수동 센서(대시보드에서 직접 지정) — 검사·적용·거부 이유·저장·재부팅 복원·TCP 드라이버·에이전트 연동.

    cd firmware && python tests/test_manual.py
"""
from __future__ import annotations

import collections
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

from jcc_ccm.manual import ManualSensors, manual_path
from jcc_ccm.predict.sensor_spec import clean, clean_list, MAX_PER_CCM
from jcc_ccm.sensors import build_drivers
from jcc_ccm.config import ModbusBusConfig
from jcc_ccm.transport import parse_sensor_config
from test_edge import _BASE, _PREDICT, _load

RTU = {"key": "h2_manual", "name": "수소(수동)", "unit": "%LEL", "driver": "modbus", "slave": 9,
       "register": 0, "type": "input", "datatype": "uint16", "scale": 0.1, "offset": 0}
TCP = {"key": "meter_kw", "name": "전력계", "unit": "kW", "driver": "modbus_tcp", "slave": 1,
       "register": 3000, "type": "holding", "datatype": "float32", "scale": 1, "offset": 0,
       "host": "192.0.2.10", "port": 502}


def test_spec_rules():
    assert clean(RTU)["slave"] == 9 and clean(TCP)["host"] == "192.0.2.10"
    bad = [dict(RTU, slave=0), dict(RTU, slave=248), dict(RTU, register=70000), dict(RTU, datatype="float64"),
           dict(RTU, type="coil"), dict(RTU, scale=0), dict(RTU, key="H2 manual"), dict(RTU, driver="can"),
           dict(TCP, host="192.0..2"), dict(TCP, host="a b"), dict(TCP, port=70000), dict(RTU, slave=True),
           dict(RTU, scale=float("nan"))]
    for b in bad:
        try:
            clean(b)
            raise AssertionError(f"통과하면 안 됨: {b}")
        except ValueError:
            pass
    assert clean(dict(TCP, slave=0))["slave"] == 0               # TCP 유닛 ID 0 허용
    ok, err = clean_list([RTU, dict(RTU), dict(RTU, key="x" * 50)])
    assert len(ok) == 1 and len(err) == 2, err
    ok, err = clean_list([dict(RTU, key=f"s{i:02d}") for i in range(MAX_PER_CCM + 2)])
    assert len(ok) == MAX_PER_CCM and len(err) == 2


def test_apply_persist_reload():
    d = tempfile.mkdtemp()
    try:
        p = os.path.join(d, "manual_sensors.json")
        m = ManualSensors(p, {"h2_lel"})
        assert m.report() == {"version": 0, "status": "", "active": [], "errors": {}}
        changed = m.apply({"version": 3, "sensors": [RTU, TCP, dict(RTU, key="h2_lel"), dict(RTU, key="bad", slave=0)]})
        r = m.report()
        assert changed and r["version"] == 3 and r["status"] == "partial" and r["active"] == ["h2_manual", "meter_kw"], r
        assert "겹칩니다" in r["errors"]["h2_lel"] and "Modbus 주소" in r["errors"]["bad"], r["errors"]
        assert not m.apply({"version": 3, "sensors": []}), "같은 버전은 무시"
        m2 = ManualSensors(p, {"h2_lel"})                            # 재부팅
        assert m2.version == 3 and [c.key for c in m2.configs()] == ["h2_manual", "meter_kw"]
        assert m2.configs()[1].driver == "modbus_tcp" and m2.configs()[1].modbus_host == "192.0.2.10"
        assert m2.apply({"version": 4, "sensors": []}) and m2.report()["active"] == [] and m2.report()["status"] == "ok"
        assert ManualSensors(p).version == 4                         # 삭제도 저장
    finally:
        shutil.rmtree(d)


def test_tcp_driver_reads_or_fails_gracefully():
    ok, _ = clean_list([TCP])
    from jcc_ccm.manual import to_config
    drv = build_drivers([to_config(ok[0])], ModbusBusConfig())
    assert len(drv) == 1
    r = drv[0].read()                     # 개발 PC: pymodbus 없음 또는 연결 실패 → 실패 값(예외 아님)
    assert not r.ok and r.key == "meter_kw" and r.error, r


def test_tcp_backoff_keeps_loop_fast():
    """연결 안 되는 TCP 장비가 매 주기 수집(=벤트 판정)을 늦추지 않게: 실패 뒤 30초는 바로 실패."""
    import time
    from jcc_ccm.sensors.modbus import ModbusTcpBus
    t = [1000.0]
    bus = ModbusTcpBus("192.0.2.12", 502, clock=lambda: t[0])
    bus._down_until = t[0] + ModbusTcpBus.RETRY_AFTER        # 방금 연결 실패한 상태
    s = time.time()
    try:
        bus.read_registers(1, 0, 1, "input")
        raise AssertionError("실패해야 함")
    except IOError as exc:
        assert "잠시 뒤 재시도" in str(exc)
    assert time.time() - s < 0.05
    t[0] += ModbusTcpBus.RETRY_AFTER + 1                    # 대기 지나면 다시 시도(여기선 pymodbus 연결 시도)
    assert bus._clock() >= bus._down_until


def test_parse_from_server():
    msg = {"ok": True, "sensor_config": {"version": 2, "sensors": [RTU]}}
    assert parse_sensor_config(msg) == msg["sensor_config"]
    for bad in ({"ok": True}, {"sensor_config": {"version": "2", "sensors": []}}, {"sensor_config": {"version": 2}},
                {"sensor_config": {"version": True, "sensors": []}}, None, []):
        assert parse_sensor_config(bad) is None, bad


def test_agent_applies_and_reports():
    from jcc_ccm.agent import Agent
    d = tempfile.mkdtemp()
    try:
        path = os.path.join(d, "predict_state.json").replace("\\", "/")
        cfg = _load((_BASE + _PREDICT).replace('state_file = ""', f'state_file = "{path}"'))
        assert manual_path(cfg).replace("\\", "/") == f"{d}/manual_sensors.json".replace("\\", "/")

        class T:
            def __init__(self):
                self.sent, self.cfg = [], None

            def send(self, p):
                self.sent.append(p)
                return True

            def take_sensor_config(self):
                out, self.cfg = self.cfg, None
                return out

        a = Agent.__new__(Agent)
        a._cfg, a._transport, a._queue, a._stop = cfg, T(), collections.deque(maxlen=50), False
        a._manual = ManualSensors(manual_path(cfg), {s.key for s in cfg.sensors})
        a._drivers = []
        a._tick()
        assert a._transport.sent[-1]["sensor_config"]["version"] == 0
        a._transport.cfg = {"version": 1, "sensors": [RTU]}
        a._tick()                                       # 이번 응답으로 받은 설정 → 적용, 드라이버 재구성
        keys = [getattr(x, "_cfg").key for x in a._drivers if hasattr(x, "_cfg")]
        assert "h2_manual" in keys, keys
        a._tick()
        last = a._transport.sent[-1]
        assert last["sensor_config"]["active"] == ["h2_manual"] and any(r["key"] == "h2_manual" for r in last["readings"])
    finally:
        shutil.rmtree(d)


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
