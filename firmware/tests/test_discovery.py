"""자동 탐색 로직 테스트 — 하드웨어 없이 도는 것만.

    cd firmware && python tests/test_discovery.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from jcc_ccm.discovery import discover_sim, identify_inventory
from jcc_ccm.discovery import profiles


def test_identify_known_profile():
    p = profiles.identify("BANNER-QM30VT")
    assert p is not None
    assert p["emits"][0]["key"] == "vibration"


def test_identify_unknown_returns_none():
    assert profiles.identify("NOPE-9999") is None


def test_multi_channel_profile_emits_two():
    # 온습도 프로파일은 온도+습도 두 채널을 낸다
    p = profiles.identify("TURCK-CCM-AMBIENT")
    keys = [e["key"] for e in p["emits"]]
    assert "cabinet_temp" in keys and "cabinet_humidity" in keys


def test_identify_inventory_confidence():
    raw = [
        {"source": "modbus", "ident": "INFRASENSING-H2", "address": 1},
        {"source": "modbus", "ident": "UNKNOWN", "address": 5, "probe": 41.5},
    ]
    out = identify_inventory(raw)
    by_key = {s["key"]: s for s in out}
    assert by_key["h2_lel"]["confidence"] == "확정"
    unknown = [s for s in out if s["confidence"] == "추정"]
    assert len(unknown) == 1
    assert unknown[0]["address"] == 5


def test_infer_temperature_range():
    g = profiles.infer({"address": 9, "probe": 24.0})
    assert g["kind"] == "temp"


def test_discover_sim_shape():
    r = discover_sim()
    assert r["panel"] == "panel-01"
    assert len(r["ccms"]) == 2
    total = sum(len(c["sensors"]) for c in r["ccms"])
    assert total == 6
    inferred = sum(1 for c in r["ccms"] for s in c["sensors"] if s["confidence"] == "추정")
    assert inferred == 1


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = 0
    for fn in fns:
        try:
            fn(); passed += 1; print(f"  PASS {fn.__name__}")
        except Exception as exc:  # noqa: BLE001
            print(f"  FAIL {fn.__name__}: {exc}")
    print(f"\n{passed}/{len(fns)} passed")
    sys.exit(0 if passed == len(fns) else 1)
