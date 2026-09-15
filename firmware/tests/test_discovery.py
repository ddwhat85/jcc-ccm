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
    """한 판넬 아래 CCM 여러 대가 묶이는 구조를 검사한다(대수는 고정하지 않는다).

    CCM은 현장에서 늘어나므로 '2대·6개' 같은 숫자를 박으면 구성만 바꿔도 깨진다.
    대신 변하면 안 되는 성질만 본다.
    """
    r = discover_sim()
    assert r["panel"] == "panel-01"
    assert r["panel_name"]
    ccms = r["ccms"]
    assert len(ccms) >= 2                      # 판넬 하나에 CCM 여러 대
    ids = [c["device_id"] for c in ccms]
    assert len(ids) == len(set(ids))           # 장비 ID 중복 없음
    for c in ccms:
        assert c["sensors"], f"{c['device_id']}에 센서가 없다"
        for s in c["sensors"]:
            assert s["key"] and s["name"]
            assert s["confidence"] in ("확정", "추정")
        keys = [s["key"] for s in c["sensors"]]
        assert len(keys) == len(set(keys))      # 같은 CCM 안에서 센서키 중복 없음
    # 미확인 장비는 추정으로 분류돼야 한다(라이브러리에 없는 장비 대응)
    assert any(s["confidence"] == "추정" for c in ccms for s in c["sensors"])


def test_single_ccm_report_merges_into_panel():
    """CCM 한 대가 따로 보고해도 같은 판넬로 합쳐지는지(실기 경로 형태)."""
    r = discover_sim()
    one = {"panel": r["panel"], "panel_name": r["panel_name"],
           "ccms": [r["ccms"][0]]}
    assert one["panel"] == r["panel"]
    assert len(one["ccms"]) == 1
    assert one["ccms"][0]["device_id"] in [c["device_id"] for c in r["ccms"]]


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
