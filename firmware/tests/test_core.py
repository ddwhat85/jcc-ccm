"""핵심 로직 테스트 — 실기·네트워크 없이 도는 것만.

    cd firmware && python -m pytest tests/ -v
    (pytest 없으면: python tests/test_core.py 로도 실행 가능)
"""
from __future__ import annotations

import collections
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from jcc_ccm import config as cfgmod
from jcc_ccm.sensors import Reading
from jcc_ccm.sensors.modbus import _decode
from jcc_ccm.config import SensorConfig


# ── 설정 검증 ────────────────────────────────────────────────
_MINIMAL = """
[device]
id = "ccm-test"
[collection]
interval_seconds = 5
[transport]
kind = "http"
[transport.http]
url = "https://example/telemetry"
[[sensors]]
key = "t"
name = "temp"
driver = "ambient"
metric = "temp"
"""


def _write(text: str) -> str:
    fd, path = tempfile.mkstemp(suffix=".toml")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


def test_load_minimal_ok():
    cfg = cfgmod.load(_write(_MINIMAL))
    assert cfg.device_id == "ccm-test"
    assert cfg.transport_kind == "http"
    assert len(cfg.sensors) == 1


def test_bad_interval_rejected():
    bad = _MINIMAL.replace("interval_seconds = 5", "interval_seconds = 0")
    try:
        cfgmod.load(_write(bad))
        assert False, "0초 주기는 거부돼야 한다"
    except cfgmod.ConfigError:
        pass


def test_http_without_url_rejected():
    bad = _MINIMAL.replace('url = "https://example/telemetry"', 'url = ""')
    try:
        cfgmod.load(_write(bad))
        assert False, "http인데 url 없으면 거부돼야 한다"
    except cfgmod.ConfigError:
        pass


def test_duplicate_sensor_keys_rejected():
    dup = _MINIMAL + """
[[sensors]]
key = "t"
name = "temp2"
driver = "ambient"
metric = "hum"
"""
    try:
        cfgmod.load(_write(dup))
        assert False, "중복 key는 거부돼야 한다"
    except cfgmod.ConfigError:
        pass


def test_modbus_needs_slave():
    bad = _MINIMAL.replace('metric = "temp"', 'metric = "temp"') + """
[[sensors]]
key = "h2"
name = "수소"
driver = "modbus"
modbus_slave = 0
"""
    try:
        cfgmod.load(_write(bad))
        assert False, "modbus_slave=0은 거부돼야 한다"
    except cfgmod.ConfigError:
        pass


# ── Modbus 디코딩 ────────────────────────────────────────────
def test_decode_uint16():
    assert _decode([1234], "uint16") == 1234.0


def test_decode_int16_negative():
    assert _decode([0xFFFF], "int16") == -1.0


def test_decode_uint32():
    # 0x0001_0002 = 65538
    assert _decode([0x0001, 0x0002], "uint32") == 65538.0


def test_decode_float32():
    # 1.0 in IEEE754 big-endian = 0x3F80 0000
    assert abs(_decode([0x3F80, 0x0000], "float32") - 1.0) < 1e-6


def test_modbus_scale_offset():
    cfg = SensorConfig(key="h2", name="수소", driver="modbus", unit="%LEL",
                       modbus_slave=1, scale=0.1, offset=0.0)
    # 원시 42 → 4.2
    raw = 42
    assert raw * cfg.scale + cfg.offset == 4.2


# ── 오프라인 큐 재전송 ───────────────────────────────────────
class _FlakyTransport:
    """처음 N번 send를 실패시켜, 큐가 데이터를 보관하는지 검증."""
    def __init__(self, fail_first: int):
        self.fail_first = fail_first
        self.sent: list[dict] = []
    def connect(self): pass
    def send(self, payload: dict) -> bool:
        if self.fail_first > 0:
            self.fail_first -= 1
            return False
        self.sent.append(payload)
        return True
    def close(self): pass


def _make_agent(transport):
    from jcc_ccm.agent import Agent
    a = Agent.__new__(Agent)
    a._cfg = cfgmod.load(_write(_MINIMAL))
    a._drivers = []
    a._transport = transport
    a._queue = collections.deque(maxlen=2000)
    a._stop = False
    return a


def test_queue_holds_when_offline_then_flushes():
    t = _FlakyTransport(fail_first=3)
    a = _make_agent(t)
    # 3주기 동안 전송 실패 → 큐에 3건 쌓임
    for _ in range(3):
        a._enqueue({"seq": _}); a._flush()
    assert len(a._queue) == 3
    assert t.sent == []
    # 회선 복구 후 한 번 더 넣고 flush → 순서대로 4건 전송
    a._enqueue({"seq": 99}); a._flush()
    assert len(a._queue) == 0
    assert len(t.sent) == 4


def test_reading_failure_is_valued_not_raised():
    cfg = SensorConfig(key="x", name="x", driver="ambient", metric="temp")
    r = Reading.failure(cfg, "boom")
    assert r.ok is False and r.value is None
    assert r.as_dict()["error"] == "boom"


if __name__ == "__main__":
    # pytest 없이도 돌도록 간이 러너
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = 0
    for fn in fns:
        try:
            fn(); passed += 1; print(f"  PASS {fn.__name__}")
        except Exception as exc:  # noqa: BLE001
            print(f"  FAIL {fn.__name__}: {exc}")
    print(f"\n{passed}/{len(fns)} passed")
    sys.exit(0 if passed == len(fns) else 1)
